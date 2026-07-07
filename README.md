# NCM Converter

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)

**网易云音乐 NCM 格式解密转换工具** —— 将 `.ncm` 加密文件还原为原始音频（MP3/FLAC/WAV/OGG），纯解密不重新编码，**音质完全不变**。

> NCM 只是对原始音频文件加了一层加密壳，本工具解密后直接写出原始音频数据，和你在网易云听到的音质一模一样。

## 🚀 快速开始（三步搞定）

### 第一步：下载工具

点击这个页面右上角的绿色 **「<> Code」** 按钮 → 点 **「Download ZIP」** → 把下载的压缩包解压到任意文件夹（比如桌面）。

### 第二步：装 Python

如果电脑已经有 Python 3.8 或更高版本，跳过这步。

**Windows 10 / 11 用户**（一键安装）：

打开开始菜单，搜 `cmd` 打开命令提示符，复制下面这行进去回车：

```bash
winget install Python.Python.3.12
```

**或者手动下载**：[python.org 下载页面](https://www.python.org/downloads/)，下载后安装，**安装时记得勾选「Add Python to PATH」**。

### 第三步：装依赖 + 开用

打开刚解压的文件夹，在地址栏输入 `cmd` 回车，然后：

```bash
pip install -r requirements.txt
```

装完之后，双击 `ncm_converter_gui.pyw`，把 `.ncm` 文件拖进去就完事了。

---

> 💡 转换后的 MP3 默认在源文件同目录。工具不会安装到系统里，删掉文件夹就等于卸载。

---

## ✨ 两种使用方式

### 🖱️ 图形界面（推荐）

双击 `ncm_converter_gui.pyw` → 把 `.ncm` 文件拖进窗口 → 自动转换。

- 支持批量拖拽
- 可指定输出目录
- 可选提取专辑封面图

### ⌨️ 命令行

```bash
# 单文件
python ncm_converter.py song.ncm

# 整个文件夹
python ncm_converter.py D:\Music\NCM\

# 指定输出目录
python ncm_converter.py *.ncm -o D:\Music\MP3\

# 递归转换 ＋ 提取封面
python ncm_converter.py D:\Music\ -r --cover
```

## 🔧 安装

```bash
# 1. Python 3.8+
python --version

# 2. 安装依赖
pip install -r requirements.txt
```

## 📖 原理

NCM 文件 = 加密的元数据 + 加密的音频流。本工具做的是纯解密还原：

```
NCM 文件结构:
┌────────────────────────────────────┐
│ [8B]  Magic: CTENFDAM              │
│ [2B]  Gap: 0x0170                  │
│ [4B]  Key len (LE)                 │
│ [*B]  Key block → XOR 0x64        │
│               → AES-128-ECB        │  → RC4 audio key
│ [*B]  Metadata → XOR 0x63         │
│               → Base64 → AES-ECB   │  → Song info JSON
│ [*B]  Cover image (raw PNG/JPG)    │
│ [*B]  Audio → Modified RC4        │  → Raw MP3/FLAC
└────────────────────────────────────┘
```

**关键：解密后直接写出原始音频，不重新编码，音质无损。**

## 📁 文件说明

| 文件 | 用途 |
|------|------|
| `ncm_converter.py` | 核心解密模块 + 命令行入口 |
| `ncm_converter_gui.pyw` | 图形界面（拖拽版） |
| `requirements.txt` | 依赖：pycryptodome, tkinterdnd2 |
| `LICENSE` | MIT 开源协议 |

## 🙏 致谢

解密算法参考了以下开源项目：
- [taurusxin/ncmdump](https://github.com/taurusxin/ncmdump)
- [allenfrostline/pyNCMDUMP](https://github.com/allenfrostline/pyNCMDUMP)

## ⚠️ 免责声明

本工具仅供学习和研究使用。请尊重版权，仅转换您合法拥有的音乐文件。
