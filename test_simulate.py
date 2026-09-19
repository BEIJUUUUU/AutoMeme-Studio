import os
import sys
import json
import time
from pathlib import Path

# 保证能导入 core 模块
CURRENT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(CURRENT_DIR))

from core.context_buffer import ContextBuffer
from core.arbiter import MemeArbiter
from core.player import AudioPlayer

def load_json(file_name: str) -> dict:
    p = CURRENT_DIR / file_name
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def print_banner():
    print("=" * 65)
    print("  AutoMeme-AI | 智能玩梗决策模拟器 (Interactive Simulator)")
    print("=" * 65)
    print("功能提示:")
    print("  - 直接输入一句话（如: '这把看我带飞'）模拟麦克风输入")
    print("  - 输入以 @ 开头模拟游戏/系统事件（如: '@玩家被敌方单杀'）")
    print("  - 输入 '!clear' 清空当前上下文窗口")
    print("  - 输入 '!exit' 或按 Ctrl+C 退出程序")
    print("=" * 65 + "\n")

def main():
    config = load_json("config.json")
    memes = load_json("meme_library.json")

    voice_dir = (CURRENT_DIR / config["audio"]["voice_pack_dir"]).resolve()
    player = AudioPlayer(base_voice_dir=str(voice_dir), volume=config["audio"]["volume"])
    buffer = ContextBuffer(
        window_seconds=config["behavior"]["context_window_seconds"],
        max_messages=config["behavior"]["max_context_messages"],
        cooldown_seconds=config["behavior"]["cooldown_seconds"]
    )
    arbiter = MemeArbiter(config, memes)

    print_banner()
    api_key = config.get("api", {}).get("api_key", "")
    if not api_key or "YOUR_API_KEY" in api_key:
        print("[提示] 检测到 config.json 中尚未填入有效 API Key。")
        print("您可以在 config.json 中填入 DeepSeek API Key，或者填入本地 Ollama 地址。")
        print("当前将以模拟应答模式运行...\n")

    while True:
        try:
            user_input = input(">> 请输入开黑对话或事件: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("!exit", "exit", "quit"):
                print("退出模拟器。")
                break
            if user_input.lower() == "!clear":
                buffer.clear()
                print("[系统] 上下文历史已清空。\n")
                continue

            # 区分输入来源
            if user_input.startswith("@"):
                source = "游戏事件"
                content = user_input[1:].strip()
            else:
                source = "麦克风"
                content = user_input

            buffer.add(source, content)

            # 检查冷却状态
            in_cd, remaining = buffer.is_in_cooldown()
            if in_cd:
                print(f"[冷却中] 距离上次音效冷却还剩 {remaining:.1f} 秒，保持静默观察...\n")
                continue

            # 获取当前上下文切片
            current_context = buffer.get_prompt_context()
            print("\n" + "-" * 40)
            print("[当前时序上下文]:")
            print(current_context)
            print("-" * 40)
            print("[AI 正在决断时机与梗选...]")

            decision = arbiter.decide(current_context)
            print(f"[决策耗时]: {decision.latency_ms:.1f}ms")
            print(f"[裁决理由]: {decision.reason}")

            if decision.should_play:
                print(f"\033[92m>>> 【命中神梗】: 《{decision.meme.title}》 (选项 {decision.choice})\033[0m")
                player.play(decision.meme.filename)
                buffer.mark_played(decision.meme.title)
            else:
                print("\033[90m>>> 【保持沉默】(时机未到或内容平常，不插话)\033[0m")

            print("\n" + "=" * 65 + "\n")

        except KeyboardInterrupt:
            print("\n退出。")
            break
        except Exception as e:
            print(f"\n[错误]: {e}\n")

if __name__ == "__main__":
    main()
