import os
import sys
import tarfile
from pathlib import Path
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CURRENT_DIR = Path(__file__).parent.resolve()
MODELS_DIR = CURRENT_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

VAD_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
SENSEVOICE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2"

def download_file(url: str, dest_path: Path, desc: str):
    if dest_path.exists():
        print(f"[已存在] {desc} -> {dest_path.name}")
        return
    print(f"[下载中] {desc}...")
    resp = requests.get(url, stream=True, verify=False, timeout=30)
    resp.raise_for_status()
    total_size = int(resp.headers.get('content-length', 0))
    downloaded = 0
    chunk_size = 1024 * 1024  # 1MB
    
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=chunk_size):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                if total_size > 0:
                    percent = (downloaded / total_size) * 100
                    mb = downloaded / (1024 * 1024)
                    print(f"\r进度: {mb:.1f}MB / {total_size/(1024*1024):.1f}MB ({percent:.1f}%)", end="", flush=True)
    print(f"\n[完成] {desc} 下载成功！")

def main():
    print("=" * 60)
    print("  AutoMeme-AI | SenseVoice-Small & Silero-VAD 自动下载配置器")
    print("=" * 60)
    
    # 1. 下载 Silero VAD
    vad_path = MODELS_DIR / "silero_vad.onnx"
    download_file(VAD_URL, vad_path, "Silero VAD 端点断句模型 (629KB)")

    # 2. 下载 SenseVoice-Small int8
    tar_path = MODELS_DIR / "sense-voice.tar.bz2"
    sense_voice_dir = MODELS_DIR / "sense-voice"
    
    if not (sense_voice_dir / "model.int8.onnx").exists():
        download_file(SENSEVOICE_URL, tar_path, "SenseVoice-Small INT8 模型 (约155MB)")
        print("[解压中] 正在解压 SenseVoice 模型包...")
        with tarfile.open(tar_path, "r:bz2") as tar:
            tar.extractall(path=MODELS_DIR)
        
        # 统一重命名解压后的目录为 sense-voice
        extracted_dir = MODELS_DIR / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
        if extracted_dir.exists():
            if sense_voice_dir.exists():
                import shutil
                shutil.rmtree(sense_voice_dir)
            extracted_dir.rename(sense_voice_dir)
            
        if tar_path.exists():
            tar_path.unlink() # 删除压缩包节约空间
        print("[完成] SenseVoice 模型解压配置完毕！")
    else:
        print(f"[已就绪] SenseVoice 模型已存在 -> {sense_voice_dir}")

    print("\n所有离线大模型已全部下载就绪！")

if __name__ == "__main__":
    main()
