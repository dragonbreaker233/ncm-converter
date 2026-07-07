#!/usr/bin/env python3
"""NCM Converter - 网易云音乐 NCM 格式解密工具.

将 NCM 加密文件纯解密还原为原始音频格式（MP3/FLAC 等）。
不重新编码，音质完全不变。

算法参考:
  - taurusxin/ncmdump (C++)
  - allenfrostline/pyNCMDUMP (Python)
"""

__version__ = "1.1.0"

import sys
import os
import re
import json
import base64
import struct
import argparse
import textwrap
from pathlib import Path

from Crypto.Cipher import AES


# ── Constants ──────────────────────────────────────────────────────
_MAGIC = b"CTENFDAM"

# Core key: 用于解密密钥块 (AES-128-ECB)
_CORE_KEY = bytes.fromhex("687a4852416d736f356b496e62617857")   # "hzHRAmso5kInbaxW"

# Meta key: 用于解密元数据块 (AES-128-ECB)
_META_KEY = bytes.fromhex("2331346c6a6b5f215c5d2630553c2728")   # "#14ljk_!\\]&0U<'\("

_XOR_KEY = 0x64   # 密钥块 XOR
_XOR_META = 0x63  # 元数据块 XOR

_CHUNK_SIZE = 1024 * 1024  # 1 MB per decryption chunk


# ── Exceptions ─────────────────────────────────────────────────────
class NCMError(Exception):
    """NCM 转换基础异常."""
    pass


class NotNCMFileError(NCMError):
    """文件不是有效的 NCM 格式."""
    pass


class DecryptionError(NCMError):
    """解密失败."""
    pass


# ── RC4 解密（修改版，用于音频流）─────────────────────────────────

def _build_rc4_sbox(key: bytes) -> bytearray:
    """标准 RC4 KSA (Key Scheduling Algorithm)."""
    s = bytearray(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xff
        s[i], s[j] = s[j], s[i]
    return s


def _rc4_decrypt_chunk(s_box: bytearray, data: bytes, start_offset: int = 0) -> bytes:
    """修改版 RC4 PRGA 解密一段数据.

    与标准 RC4 的区别：
    - j 不累积（j = i + s[i]，而非 j = (j + s[i])）
    - 不交换 s[i] 和 s[j]
    - 这使得密钥流每 256 字节重复，支持分块并行解密

    Args:
        s_box: 初始 S-Box（KSA 结果）
        data: 待解密数据
        start_offset: 在全局音频流中的起始偏移（用于密钥流定位）
    """
    result = bytearray(len(data))
    for idx in range(len(data)):
        i = (start_offset + idx + 1) & 0xff
        j = (i + s_box[i]) & 0xff
        k = s_box[(s_box[i] + s_box[j]) & 0xff]
        result[idx] = data[idx] ^ k
    return bytes(result)


# ── NCM 解密核心 ──────────────────────────────────────────────────

def decrypt_ncm_header(file_path: str) -> dict:
    """读取并解密 NCM 文件头，返回密钥块信息.

    Returns:
        {
            "rc4_key": bytes,      # 音频流 RC4 密钥
            "metadata": dict,       # 歌曲元数据 (JSON)
            "cover_data": bytes,    # 封面图原始数据 (可为空)
            "audio_offset": int,    # 音频流在文件中的起始偏移
            "audio_size": int,      # 音频流字节数
        }
    """
    with open(file_path, "rb") as f:
        # ── 1. 验证文件头 ──
        magic = f.read(8)
        if magic != _MAGIC:
            raise NotNCMFileError(
                f"文件头不匹配: 期望 'CTENFDAM', "
                f"实际 '{magic.decode('ascii', errors='replace')}'"
            )

        # ── 2. 读取密钥块长度（跳过 2 字节 gap） ──
        f.seek(2, 1)  # gap: 0x01 0x70（固定值，跳过即可）
        key_len_bytes = f.read(4)
        if len(key_len_bytes) != 4:
            raise DecryptionError("文件太短，无法读取密钥长度")
        key_len = struct.unpack("<I", key_len_bytes)[0]

        # ── 3. 读取并解密密钥块 → 得到 RC4 密钥 ──
        key_data = f.read(key_len)
        if len(key_data) != key_len:
            raise DecryptionError(
                f"密钥数据不完整: 期望 {key_len} 字节, 实际 {len(key_data)} 字节"
            )

        # 3a. XOR 0x64
        xored = bytes(b ^ _XOR_KEY for b in key_data)
        # 3b. AES-128-ECB 解密
        cipher = AES.new(_CORE_KEY, AES.MODE_ECB)
        decrypted = cipher.decrypt(xored)

        # 3c. 校验前缀
        if decrypted[:17] != b"neteasecloudmusic":
            raise DecryptionError(
                "密钥块解密后标志位不匹配，文件可能已损坏"
            )

        # 3d. 去除前缀 (17字节) 和 PKCS7 padding
        body = decrypted[17:]
        pad_len = body[-1]
        if 0 < pad_len <= 16:
            rc4_key = body[:-pad_len]
        else:
            rc4_key = body

        # ── 4. 读取并解密元数据 ──
        meta_len_bytes = f.read(4)
        if len(meta_len_bytes) != 4:
            raise DecryptionError("无法读取元数据长度")
        meta_len = struct.unpack("<I", meta_len_bytes)[0]

        meta_raw = f.read(meta_len)
        if len(meta_raw) != meta_len:
            raise DecryptionError("元数据不完整")

        metadata = _decrypt_metadata(meta_raw)

        # ── 5. 跳过 CRC + 封面图（不加密，直接读取） ──
        f.read(4)   # CRC32 of cover image
        f.read(5)   # 5 bytes gap
        cover_len_bytes = f.read(4)
        cover_len = struct.unpack("<I", cover_len_bytes)[0] if len(cover_len_bytes) == 4 else 0

        cover_data = b""
        if cover_len > 0 and cover_len < 50 * 1024 * 1024:  # 合理性检查: < 50MB
            cover_data = f.read(cover_len)

        # ── 6. 记录音频流偏移 ──
        audio_offset = f.tell()
        f.seek(0, 2)
        audio_size = f.tell() - audio_offset

    return {
        "rc4_key": rc4_key,
        "metadata": metadata,
        "cover_data": cover_data,
        "audio_offset": audio_offset,
        "audio_size": audio_size,
    }


def _decrypt_metadata(raw: bytes) -> dict:
    """解密 NCM 元数据块.

    格式: XOR 0x63 → "N key(Don't modify):<base64>" → base64解码 → AES-ECB
    """
    # 1. XOR 0x63
    xored = bytes(b ^ _XOR_META for b in raw)

    # 2. 解析 "N key(Don't modify):<base64>"
    meta_str = xored.decode("utf-8", errors="replace")
    if "key(Don't modify):" not in meta_str:
        raise DecryptionError("元数据格式异常：缺少 key(Don't modify) 前缀")

    b64_data = meta_str.split("key(Don't modify):", 1)[1]

    # 3. Base64 解码
    try:
        encrypted_meta = base64.b64decode(b64_data)
    except Exception as e:
        raise DecryptionError(f"元数据 Base64 解码失败: {e}")

    # 4. 补齐到 16 字节边界
    pad_needed = (16 - len(encrypted_meta) % 16) % 16
    if pad_needed:
        encrypted_meta += bytes([0] * pad_needed)

    # 5. AES-128-ECB 解密
    cipher = AES.new(_META_KEY, AES.MODE_ECB)
    decrypted = cipher.decrypt(encrypted_meta)

    # 6. 去除 PKCS7 padding
    pad = decrypted[-1]
    if 0 < pad <= 16:
        decrypted = decrypted[:-pad]

    # 7. 解析 JSON（去掉可能的 "music:" 前缀）
    json_str = decrypted.decode("utf-8", errors="replace")
    if json_str.startswith("music:"):
        json_str = json_str[6:]

    # 8. 尝试提取 JSON 对象（处理尾部垃圾数据）
    brace_depth = 0
    json_end = 0
    for i, ch in enumerate(json_str):
        if ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
            if brace_depth == 0:
                json_end = i + 1
                break

    if json_end == 0:
        raise DecryptionError("无法从元数据中解析 JSON")

    try:
        return json.loads(json_str[:json_end])
    except json.JSONDecodeError as e:
        raise DecryptionError(f"元数据 JSON 解析失败: {e}")


def decrypt_audio_stream(file_path: str, header_info: dict) -> bytes:
    """用修改版 RC4 解密音频流，分块处理以控制内存."""
    rc4_key = header_info["rc4_key"]
    audio_offset = header_info["audio_offset"]
    audio_size = header_info["audio_size"]

    # 构建 S-Box（只需要一次 KSA）
    s_box = _build_rc4_sbox(rc4_key)

    result = bytearray()
    with open(file_path, "rb") as f:
        f.seek(audio_offset)
        offset = 0
        while offset < audio_size:
            chunk_size = min(_CHUNK_SIZE, audio_size - offset)
            encrypted_chunk = f.read(chunk_size)
            result.extend(_rc4_decrypt_chunk(s_box, encrypted_chunk, offset))
            offset += chunk_size

    return bytes(result)


# ── Metadata & Naming ──────────────────────────────────────────────

def _sanitize_filename(s: str) -> str:
    """替换 Windows 文件名中的非法字符为下划线."""
    return re.sub(r'[\\/:*?"<>|]', "_", s).strip()


def _is_garbled(s: str) -> bool:
    """检测字符串是否可能是乱码（含大量非 ASCII 可打印字符）."""
    if not s:
        return True
    # 乱码特征：含大量 latin-1 控制字符或不可打印字符
    bad = sum(1 for c in s if ord(c) < 32 or (128 <= ord(c) < 160))
    return bad > len(s) * 0.3


def generate_filename(metadata: dict, audio_format: str, input_stem: str) -> str:
    """根据元数据生成输出文件名.

    格式: "歌手 - 歌名.扩展名"
    当元数据乱码时回退到输入文件名。
    """
    # 歌手
    artists = metadata.get("artist")
    artist_name = ""
    if artists and isinstance(artists, list) and len(artists) > 0:
        if isinstance(artists[0], list):
            artist_name = str(artists[0][0])
        else:
            artist_name = str(artists[0])

    # 歌曲名
    song_name = metadata.get("musicName", "") or ""

    # 如果关键字段乱码或为空，回退到输入文件名
    if _is_garbled(artist_name) or not artist_name:
        artist_name = ""
    if _is_garbled(song_name) or not song_name:
        song_name = input_stem

    # 格式
    fmt = metadata.get("format", audio_format)
    if fmt and fmt.lower() in ("mp3", "flac", "wav", "ogg", "m4a", "wma"):
        fmt = fmt.lower()

    if artist_name:
        return f"{_sanitize_filename(artist_name)} - {_sanitize_filename(song_name)}.{fmt}"
    else:
        return f"{_sanitize_filename(song_name)}.{fmt}"


def detect_format(decrypted_audio: bytes) -> str:
    """通过 magic bytes 检测解密后音频的真实格式."""
    if len(decrypted_audio) < 4:
        return "unknown"
    if decrypted_audio[:3] == b"ID3":
        return "mp3"
    if decrypted_audio[:4] == b"fLaC":
        return "flac"
    if decrypted_audio[:4] == b"RIFF":
        return "wav"
    if decrypted_audio[:4] == b"OggS":
        return "ogg"
    if decrypted_audio[0] == 0xFF and (decrypted_audio[1] & 0xE0) == 0xE0:
        return "mp3"
    return "unknown"


# ── File Processing ────────────────────────────────────────────────

def convert_file(
    input_path: str,
    output_dir: str | None = None,
    force: bool = False,
    extract_cover: bool = False,
) -> tuple:
    """转换单个 NCM 文件.

    Returns:
        (success: bool, message: str)
    """
    input_path = os.path.abspath(input_path)
    if output_dir is None:
        output_dir = os.path.dirname(input_path)

    # ── 1. 解密文件头 ──
    try:
        header = decrypt_ncm_header(input_path)
    except PermissionError as e:
        return False, f"无法读取文件: {e}"
    except (NotNCMFileError, DecryptionError, json.JSONDecodeError) as e:
        return False, f"解密失败: {e}"

    metadata = header["metadata"]
    input_stem = Path(input_path).stem

    # ── 2. 解密音频流 ──
    try:
        decrypted_audio = decrypt_audio_stream(input_path, header)
    except Exception as e:
        return False, f"音频流解密失败: {e}"

    # ── 3. 检测格式 ＋ 生成文件名 ──
    audio_format = detect_format(decrypted_audio)
    output_name = generate_filename(metadata, audio_format, input_stem)
    output_path = os.path.join(output_dir, output_name)

    # ── 4. 检查是否已存在 ──
    if os.path.exists(output_path) and not force:
        return False, f"输出文件已存在: {output_path} （使用 --force 覆盖）"

    # ── 5. 写入音频 ──
    os.makedirs(output_dir, exist_ok=True)
    try:
        with open(output_path, "wb") as f:
            f.write(decrypted_audio)
    except PermissionError as e:
        return False, f"无法写入输出文件: {e}"

    # ── 6. 可选：写出封面图 ──
    cover_msg = ""
    if extract_cover and header.get("cover_data"):
        try:
            cover_path = os.path.join(output_dir, Path(output_name).stem + ".jpg")
            with open(cover_path, "wb") as f:
                f.write(header["cover_data"])
            cover_msg = f", 封面已保存: {os.path.basename(cover_path)}"
        except Exception as e:
            cover_msg = f", 封面提取失败: {e}"

    return True, f"[OK] {output_name}{cover_msg}"


# ── Batch Discovery ────────────────────────────────────────────────

def discover_ncm_files(inputs: list, recursive: bool = False) -> list:
    """将用户输入的路径/通配符/文件夹展开为 .ncm 文件列表."""
    import glob as glob_mod

    ncm_files: list = []

    for item in inputs:
        if any(c in item for c in "*?["):
            matches = glob_mod.glob(item, recursive=recursive)
            ncm_files.extend(os.path.abspath(m) for m in matches)
        elif os.path.isdir(item):
            pattern = os.path.join(item, "**" if recursive else "", "*.ncm")
            matches = glob_mod.glob(pattern, recursive=recursive)
            ncm_files.extend(os.path.abspath(m) for m in matches)
        elif os.path.isfile(item):
            ncm_files.append(os.path.abspath(item))
        else:
            print(f"警告: 找不到 '{item}'，已跳过", file=sys.stderr)

    # 去重
    seen: set = set()
    unique: list = []
    for f in ncm_files:
        if f.lower() not in seen:
            seen.add(f.lower())
            unique.append(f)
    return unique


# ── Main ───────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="ncm_converter",
        description=textwrap.dedent("""\
            NCM Converter —— 网易云音乐 NCM 格式解密转换工具
            ─────────────────────────────────────────────────
            将 NCM 加密文件纯解密还原为原始音频格式（MP3/FLAC 等）。
            不重新编码，音质完全不变。
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            使用示例:
              python ncm_converter.py song.ncm
              python ncm_converter.py D:\\\\Music\\\\NCM\\\\
              python ncm_converter.py *.ncm -o D:\\\\Music\\\\MP3\\\\
              python ncm_converter.py D:\\\\Music\\\\ -r --dry-run
              python ncm_converter.py song.ncm --cover
        """),
    )
    parser.add_argument(
        "input", nargs="+",
        help="一个或多个 .ncm 文件路径、文件夹路径、或通配符",
    )
    parser.add_argument(
        "-o", "--output", dest="output_dir", default=None,
        help="输出目录（默认: 与输入文件相同目录）",
    )
    parser.add_argument(
        "-f", "--force", action="store_true",
        help="覆盖已存在的输出文件",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true",
        help="静默模式",
    )
    parser.add_argument(
        "-r", "--recursive", action="store_true",
        help="递归搜索子文件夹",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅列出将要转换的文件，不实际执行",
    )
    parser.add_argument(
        "--cover", action="store_true",
        help="同时提取封面图（保存为 .jpg）",
    )
    parser.add_argument(
        "-v", "--version", action="version",
        version=f"ncm_converter {__version__}",
    )

    args = parser.parse_args()

    # 发现文件
    ncm_files = discover_ncm_files(args.input, args.recursive)
    if not ncm_files:
        print("错误: 未找到任何 .ncm 文件", file=sys.stderr)
        return 2

    # 预览模式
    if args.dry_run:
        print(f"将要转换 {len(ncm_files)} 个文件:")
        for f in ncm_files:
            print(f"  {f}")
        return 0

    # 逐文件转换
    success_count = 0
    fail_count = 0
    skip_count = 0

    for i, f in enumerate(ncm_files, 1):
        if not args.quiet:
            print(f"[{i}/{len(ncm_files)}] {os.path.basename(f)}")

        ok, msg = convert_file(f, args.output_dir, args.force, args.cover)

        if ok:
            success_count += 1
            if not args.quiet:
                print(f"  {msg}")
        else:
            if "已存在" in msg:
                skip_count += 1
            else:
                fail_count += 1
            print(f"  {msg}", file=sys.stderr)

    # 汇总
    if not args.quiet:
        print(f"\n{'─' * 50}")
        print(f"完成: 成功 {success_count}", end="")
        if skip_count:
            print(f", 跳过 {skip_count}", end="")
        if fail_count:
            print(f", 失败 {fail_count}", end="")
        print()

    if fail_count == 0 and skip_count == 0:
        return 0
    elif success_count > 0:
        return 1
    else:
        return 2


if __name__ == "__main__":
    sys.exit(main())
