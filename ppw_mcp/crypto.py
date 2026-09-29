"""排排网 sppwapi 响应解密链。

服务端对 sppwapi.simuwang.com 的 JSON 响应做动态加密，结构为::

    {"data": {"encode": <int>, "key": "<混淆JS>", "id": "<window属性名>", "data": "<密文>"}}

前端解密步骤（逆向自 dc.simuwang.com 入口 JS）：
1. 执行 data.key 中的 JS，得到 window[id]（一个短字符串）。
   该 JS 是确定性变换：模式串按分隔符分段、逐字符映射为数字、
   按进制解析成整数、减偏移得到字节，最后 UTF-8 解码为
   `window.<id>="..."` 形式的赋值语句。这里用纯 Python 复刻，
   不执行任何 JS。
2. 按 encode 编号对 key 字符串做切片变形（3=reverse, 4=slice(2), ...）。
3. md5 = MD5(变形后key) 的十六进制串；AES-256-CBC，key=md5 全串
   （32 字节），iv=md5[16:32]，PKCS7。
4. 密文为双重 base64（CryptoJS.AES.decrypt 会对 atob 结果再
   Base64.parse 一次），解出的明文为 JSON。
"""

from __future__ import annotations

import base64
import hashlib
import json
import re

try:  # pycryptodome
    from Crypto.Cipher import AES
except ImportError:  # pycryptodome 无 pycrypto 别名时的备用
    from Cryptodome.Cipher import AES  # type: ignore

# 前端 decrypt() 中 encode 编号 -> key 变形规则
_ENCODE_TRANSFORMS = {
    3: lambda k: k[::-1],
    4: lambda k: k[2:],
    5: lambda k: k[:-2],
    6: lambda k: k[1:-1],
    7: lambda k: k[2:-1],
    8: lambda k: k[1:-2],
    9: lambda k: k[0] + k[2:],
    10: lambda k: k[:-2] + k[-1],
}

_ASSIGN_RE = re.compile(r'=[\'"]([^\'"]+)[\'"]')


def _run_key_js(js_text: str) -> str:
    """复刻 data.key 混淆 JS，返回其还原出的赋值语句源码。

    JS 形如 ``eval(function(h,u,n,t,e,r){...}("模式串",45,"字母表",49,2,11))``：
    模式串按 n[e] 分段，段内字符按其在 n 中的序号替换为数字，
    按 e 进制解析为整数，减 t 得到字节码。
    """
    start = js_text.rfind('}("')
    if start == -1:
        raise ValueError("key JS 中未找到 eval 参数段")
    rest = js_text[start + 3 : js_text.rfind("))")]
    toks = rest.split('"')
    if len(toks) < 4:
        raise ValueError("key JS 参数结构异常")
    pattern, n_table = toks[0], toks[2]
    nums = [x for x in toks[3].split(",") if x.strip().isdigit()]
    if len(nums) < 2:
        raise ValueError("key JS 缺少进制/偏移参数")
    t, e = int(nums[0]), int(nums[1])
    sep = n_table[e]
    out = bytearray()
    for seg in pattern.split(sep):
        if not seg:
            continue
        for idx, ch in enumerate(n_table):
            seg = seg.replace(ch, str(idx))
        out.append(int(seg, e) - t)
    return out.decode("utf-8")


def _extract_key(assignment: str) -> str:
    """从 ``window.xxx="value"`` 语句中提取字符串值。"""
    m = _ASSIGN_RE.search(assignment)
    if not m:
        raise ValueError(f"key JS 未还原出赋值语句: {assignment[:80]!r}")
    return m.group(1)


def decode_response_payload(payload: dict) -> dict | list | None:
    """解密 sppwapi 响应的 data 节点，返回明文 JSON 对象。

    对 encode == "aes"（微信小程序通道，key=MD5(wxSign)）不支持，
    Web 端不会出现；未加密负载原样返回。
    """
    if not isinstance(payload, dict):
        return payload
    encode = payload.get("encode")
    if not encode or encode == "aes":
        return payload

    key_js = payload.get("key", "")
    enc_id = payload.get("id", "")
    ciphertext = payload.get("data", "")
    if not (key_js and enc_id and ciphertext):
        return payload

    assignment = _run_key_js(key_js)
    key_raw = _extract_key(assignment)
    transform = _ENCODE_TRANSFORMS.get(int(encode), lambda k: k)
    key = transform(key_raw)
    md5 = hashlib.md5(key.encode()).hexdigest()

    ct = base64.b64decode(base64.b64decode(ciphertext))
    cipher = AES.new(md5.encode(), AES.MODE_CBC, md5[16:32].encode())
    pt = cipher.decrypt(ct)
    pt = pt[: -pt[-1]]
    return json.loads(pt.decode("utf-8"))
