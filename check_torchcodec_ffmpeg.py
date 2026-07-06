import os, sys

# Add DLL paths so torchcodec can find its dependencies
tc_path = r"D:\Base\miniconda3\envs\GPTSoVits\Lib\site-packages\torchcodec"
torch_lib = r"D:\Base\miniconda3\envs\GPTSoVits\Lib\site-packages\torch\lib"
cuda_bin = r"D:\Base\miniconda3\envs\GPTSoVits\Library\bin"

os.add_dll_directory(tc_path)
os.add_dll_directory(torch_lib)
os.add_dll_directory(cuda_bin)

print("DLL search paths added")

# Check what's in the torchcodec package
import subprocess
result = subprocess.run(['where', 'libtorchcodec_core8.dll'], capture_output=True, text=True)
print("where libtorchcodec_core8.dll:", result.stdout.strip() or "not in PATH")

# Check FFmpeg DLLs that torchcodec might need
# torchcodec bundles FFmpeg as part of its wheel
ffmpeg_dlls = ['avcodec-60.dll', 'avdevice-60.dll', 'avfilter-9.dll', 
                'avformat-60.dll', 'avutil-58.dll', 'swresample-4.dll', 'swscale-7.dll']
print("\nChecking FFmpeg DLLs in torchcodec package:")
for dll in ffmpeg_dlls:
    p = os.path.join(tc_path, dll)
    print(f"  {dll}: {'EXISTS' if os.path.exists(p) else 'MISSING'}")

# Also check parent dirs
for check_path in [torch_lib, cuda_bin, r"C:\Windows\System32"]:
    for dll in ffmpeg_dlls:
        p = os.path.join(check_path, dll)
        if os.path.exists(p):
            print(f"  Found {dll} in {check_path}")

# Now try to load torchcodec
print("\n--- Trying torchcodec import ---")
try:
    import torchcodec
    print("SUCCESS! torchcodec version:", torchcodec.__version__)
    
    # Try loading a WAV file
    test_wav = r"D:\Base\miniconda3\envs\GPTSoVits\Lib\site-packages\torchcodec\libtorchcodec_core8.dll"
    print("Testing torchcodec API...")
    
    # Check what decoders are available
    print("Available decoders:", dir(torchcodec.decoders))
except Exception as e:
    print("FAILED:", e)

# Also check what torchaudio's backend is
print("\n--- Checking torchaudio ---")
import torchaudio
print("torchaudio version:", torchaudio.__version__)

# Check if torchaudio uses torchcodec
try:
    info = torchaudio.info(test_wav)
    print("torchaudio.info works:", info)
except Exception as e:
    print("torchaudio.info failed:", e)

# Check if there's a way to see the backend
print("\n--- Checking audio backends ---")
try:
    # In newer torchaudio versions
    print("torchaudio.available_backends:", torchaudio.list_audio_backends() if hasattr(torchaudio, 'list_audio_backends') else "N/A")
except Exception as e:
    print("Error:", e)
