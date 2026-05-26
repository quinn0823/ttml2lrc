import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

# ------------------------
# 工具函数：时间处理
# ------------------------

def parse_ttml_time(value: str) -> int:
    """
    将 Apple Music TTML 时间格式转换为毫秒（int）

    支持：
      - SS.mmm
      - M:SS.mmm
      - MM:SS.mmm
      - H:MM:SS.mmm
      - HH:MM:SS.mmm
    """
    value = value.strip()

    parts = value.split(":")

    # 从右向左解析
    millis = 0
    multiplier = 1

    for part in reversed(parts):
        if "." in part:
            sec_part, frac_part = part.split(".", 1)
            sec = int(sec_part) if sec_part else 0
            frac_ms = int((frac_part + "000")[:3])
            part_ms = sec * 1000 + frac_ms
        else:
            part_ms = int(part) * 1000

        millis += part_ms * multiplier
        multiplier *= 60

    return millis


def format_lrc_time(milliseconds: int) -> str:
    """
    毫秒 -> [MM:SS.mm]
    """
    total_centis = int(Decimal(milliseconds / 10).quantize(0, rounding=ROUND_HALF_UP))
    minutes = total_centis // 6000
    centis = total_centis % 6000
    seconds = centis // 100
    hundredths = centis % 100
    return f"[{minutes:02d}:{seconds:02d}.{hundredths:02d}]"


# ------------------------
# TTML -> LRC 转换
# ------------------------

def convert_ttml_to_lrc(ttml_path: Path) -> list[str]:
    """
    解析 TTML，返回 LRC 行列表（不写文件）
    """
    ns = {
        "tt": "http://www.w3.org/ns/ttml",
        "ttm": "http://www.w3.org/ns/ttml#metadata",
        "itunes": "http://music.apple.com/lyric-ttml-internal",
    }

    tree = ET.parse(ttml_path)
    root = tree.getroot()

    body = root.find("tt:body", ns)
    if body is None:
        raise ValueError("TTML body not found")

    # -------- 歌词结构收集 --------
    items = []  # {"type": "line"|"gap", ...}

    for div in body.findall("tt:div", ns):
        last_end = None

        for p in div.findall("tt:p", ns):
            begin = parse_ttml_time(p.attrib["begin"])
            end = parse_ttml_time(p.attrib["end"])
            text = (p.text or "").strip()

            items.append({
                "type": "line",
                "begin": begin,
                "end": end,
                "text": text
            })

            last_end = end

        # 段落空行（gap）
        if last_end is not None:
            items.append({
                "type": "gap",
                "time": last_end
            })

    # # -------- 时间戳递增修正（每一行都检查） --------
    # for i, item in enumerate(items):
    #     # 当前项时间：line 使用 begin，gap 使用 time
    #     if item["type"] == "gap":
    #         current_time = item["time"]
    #     else:
    #         current_time = item["begin"]

    #     # 找到下一个 line
    #     next_begin = None
    #     for j in range(i + 1, len(items)):
    #         if items[j]["type"] == "line":
    #             next_begin = items[j]["begin"]
    #             break

    #     if next_begin is not None and current_time > next_begin:
    #         if item["type"] == "gap":
    #             item["time"] = next_begin
    #         else:
    #             item["begin"] = next_begin
    #             # 保持 end 不小于 begin，以避免后续逻辑异常
    #             if item["end"] < item["begin"]:
    #                 item["end"] = item["begin"]

    # -------- 作曲者 --------
    songwriters = []
    for sw in root.findall(".//itunes:songwriters/itunes:songwriter", ns):
        if sw.text:
            songwriters.append(sw.text.strip())

    # -------- 生成 LRC --------
    lrc_lines = []

    last_time = None

    for item in items:
        if item["type"] == "line":
            last_time = item["end"]
            lrc_lines.append(
                f"{format_lrc_time(item['begin'])} {item['text']}"
            )
        else:
            last_time = item["time"]
            lrc_lines.append(
                f"{format_lrc_time(item['time'])}"
            )

    # Written by（直接接在最后一个空行后）
    if songwriters and last_time is not None:
        lrc_lines.append(
            f"{format_lrc_time(last_time)} Written by: {', '.join(songwriters)}"
        )

    return lrc_lines


# ------------------------
# 输入链接获取
# ------------------------

def get_urls_from_input(cfg: dict) -> list[str]:
    urls = []

    if cfg["use_clipboard"]:
        try:
            import pyperclip
        except ImportError:
            raise RuntimeError("use_clipboard=true 但未安装 pyperclip")

        text = pyperclip.paste()
        lines = text.splitlines()
    else:
        with open(cfg["input_file"], "r", encoding="utf-8") as f:
            lines = f.readlines()

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)

    return urls


# ------------------------
# 主流程
# ------------------------

def main():
    # 读取配置
    with open("config.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)

    apple_music_dir = Path(cfg["apple_music_dir"])
    output_dir = Path(cfg["output_dir"])
    archive_dir = Path(cfg["ttml_archive_dir"])

    apple_music_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)
    archive_dir.mkdir(exist_ok=True)

    # -------- 调用 gamdl --------
    urls = get_urls_from_input(cfg)

    for url in urls:
        cmd = cfg["gamdl_command"] + [url] + cfg["gamdl_args"]
        print("Running:", " ".join(cmd))
        subprocess.run(cmd, check=False)

    # -------- 遍历 TTML 并转换 --------
    for ttml_path in apple_music_dir.rglob("*.ttml"):
        # 相对路径（相对于 Apple Music）
        rel_path = ttml_path.relative_to(apple_music_dir)

        # 输出 LRC 路径
        lrc_path = (output_dir / rel_path).with_suffix(".lrc")
        lrc_path.parent.mkdir(parents=True, exist_ok=True)

        print("Converting:", ttml_path)

        try:
            lrc_lines = convert_ttml_to_lrc(ttml_path)

            with open(lrc_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lrc_lines))

            # -------- 归档 TTML（覆盖）--------
            archive_ttml_path = archive_dir / rel_path
            archive_ttml_path.parent.mkdir(parents=True, exist_ok=True)

            if archive_ttml_path.exists():
                archive_ttml_path.unlink()

            shutil.move(str(ttml_path), str(archive_ttml_path))

        except Exception as e:
            print("ERROR:", ttml_path)
            print(e)


if __name__ == "__main__":
    main()
