import os, sys

# Check torchcodec DLL dependencies
dll_path = r"D:\Base\miniconda3\envs\GPTSoVits\Lib\site-packages\torchcodec\libtorchcodec_core8.dll"

# List all DLLs in the torchcodec package
tc_path = r"D:\Base\miniconda3\envs\GPTSoVits\Lib\site-packages\torchcodec"
dlls = [f for f in os.listdir(tc_path) if f.endswith('.dll')]
print("torchcodec DLLs:", dlls)

# Check torch package for DLLs
torch_path = r"D:\Base\miniconda3\envs\GPTSoVits\Lib\site-packages\torch"
torch_dlls = [f for f in os.listdir(torch_path) if f.endswith('.dll')]
print("torch DLLs count:", len(torch_dlls))

# Check CUDA DLLs in conda env
cuda_path = r"D:\Base\miniconda3\envs\GPTSoVits\Library\bin"
if os.path.exists(cuda_path):
    cuda_dlls = [f for f in os.listdir(cuda_path) if f.endswith('.dll')]
    print("CUDA DLLs count:", len(cuda_dlls))
else:
    print("No CUDA bin directory found")

# Try to load torchcodec with extended DLL search path
import ctypes

# Add torchcodec path to DLL search
ctypes.windll.kernel32.SetDllDirectoryW(tc_path)

# Try loading the DLL directly
try:
    lib = ctypes.CDLL(dll_path)
    print("SUCCESS: libtorchcodec_core8.dll loaded!")
except OSError as e:
    print("FAILED:", e)
    
    # Check what DLLs are missing
    import subprocess
    result = subprocess.run(
        ['dumpbin', '/dependents', dll_path] if os.path.exists(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vcvarsall.bat") else ['where', 'dumpbin'],
        capture_output=True, text=True
    )
    print("dumpbin output:", result.stdout[:500], result.stderr[:500])

# Check system32 for critical DLLs
system32 = r"C:\Windows\System32"
for dll in ['vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll']:
    p = os.path.join(system32, dll)
    print(f"  {dll}: {'EXISTS' if os.path.exists(p) else 'MISSING'}")

# Try loading through ctypes with explicit paths
print("\n--- Trying explicit DLL loading ---")
os.add_dll_directory(tc_path)

# Check if torch package DLLs are accessible
torch_lib_path = r"D:\Base\miniconda3\envs\GPTSoVits\Lib\site-packages\torch\lib"
if os.path.exists(torch_lib_path):
    os.add_dll_directory(torch_lib_path)
    print(f"Added torch lib path: {torch_lib_path}")

# Check cuda bin
cuda_bin = r"D:\Base\miniconda3\envs\GPTSoVits\Library\bin"
if os.path.exists(cuda_bin):
    os.add_dll_directory(cuda_bin)
    print(f"Added CUDA bin path: {cuda_bin}")

try:
    import torchcodec
    print("torchcodec imported successfully!")
    print("Version:", torchcodec.__version__)
except Exception as e:
    print("torchcodec import FAILED:", e)
