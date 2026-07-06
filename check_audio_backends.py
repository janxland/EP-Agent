import torchaudio
print("torchaudio version:", torchaudio.__version__)
print("Available backends:", torchaudio.list_audio_backends())

# Try to load a WAV file with torchaudio
import os, tempfile
test_wav = r"D:\Base\miniconda3\envs\GPTSoVits\Lib\site-packages\torchcodec\libtorchcodec_core8.dll"
print("\ntorchcodec DLL path exists:", os.path.exists(test_wav))

# Check what's needed for torchcodec
import subprocess
result = subprocess.run(
    ["where", "libtorchcodec_core8.dll"],
    capture_output=True, text=True
)
print("torchcodec DLL location:", result.stdout.strip() or "not in PATH")

# Check MSVC runtime
msvc_path = r"C:\Windows\System32\vcruntime140.dll"
print("vcruntime140.dll exists:", os.path.exists(msvc_path))
msvc_path2 = r"C:\Windows\System32\vcruntime140_1.dll"
print("vcruntime140_1.dll exists:", os.path.exists(msvc_path2))

# Check if api_v2.py uses torchcodec
# First find it
import sys
gptsovits_paths = [p for p in sys.path if "GPTSoVits" in p]
print("\nGPTSoVits paths:", gptsovits_paths[:3])

# Check if there's an api_v2.py nearby
for p in sys.path:
    if "GPTSoVits" in p:
        api_path = os.path.join(os.path.dirname(p), "api_v2.py")
        if os.path.exists(api_path):
            print("Found api_v2.py:", api_path)
