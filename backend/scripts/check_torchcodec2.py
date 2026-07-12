# Check torchcodec compatibility with PyTorch 2.11
# The issue: torchcodec 0.14.0 may require PyTorch nightly or specific version
# Let's check what PyTorch nightly builds support

import sys
print("Python:", sys.version)
print("PyTorch:", end=" ")
import torch
print(torch.__version__)
print("CUDA:", torch.version.cuda or "CPU")

# Check if torchcodec supports PyTorch 2.11
# Per torchcodec docs: https://github.com/pytorch/torchcodec
# torchcodec 0.14.0 requires PyTorch 2.5.0 or nightly
# PyTorch 2.11.0 is a stable release that should be compatible

# The real issue is the DLL loading on Windows
# Let's check if the DLL files exist
import os
tc_path = r"D:\Base\miniconda3\envs\GPTSoVits\Lib\site-packages\torchcodec"
dll_files = [f for f in os.listdir(tc_path) if f.endswith('.dll')]
print("torchcodec DLL files:", dll_files)

# Check FFmpeg DLLs
ffmpeg_dll = os.path.join(tc_path, "libtorchcodec_core8.dll")
print("libtorchcodec_core8.dll exists:", os.path.exists(ffmpeg_dll))
