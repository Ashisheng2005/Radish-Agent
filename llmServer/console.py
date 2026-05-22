import argparse
import os
import traceback
import time
import sys
from pathlib import Path

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.formatted_text import ANSI
    from prompt_toolkit.completion import Completer, Completion
    
except Exception:
    PromptSession = None
    InMemoryHistory = None
    Completer = Completion = None

    def patch_stdout():
        class _NullContext:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        return _NullContext()

from llmPolling import Polling

# 　　       ∧,,　
# 　　　　 ヾ｀. ､`フ
# 　　　(,｀'´ヽ､､ﾂﾞ
# 　 (ヽｖ'　　　`''ﾞつ
# 　　,ゝ　 ⌒`ｙ'''´
# 　 （ (´＾ヽこつ
# 　　 ) )
# 　　(ノ



HELP_TEXT = """可用命令:
/help                显示帮助
/clear               清空会话上下文
/mode                查看当前任务模式（ask/plan/agent/auto）
/mode ask|plan|agent|auto  切换任务模式（auto 为自动识别）
/budget              查看工具预算
/budget rounds N     设置每次对话的最大工具轮次
/budget per_round N  设置每轮最大工具调用数
/budget reset        重置预算为配置默认值
/debug on|off        打开/关闭调试输出
/usage on|off        打开/关闭每次回复后的使用统计显示
/setup [refresh]     打开 LLM 提供商配置向导 / 刷新模型数据库缓存
/switch [提供商]      切换当前模型（不指定则交互选择）
/graph               查看代码图状态（项目路径、节点/边数量）
/graph status        同 /graph
/graph build         为当前目录构建 CODE_GRAPH 索引
/graph gate clear    清空符号读取门禁（调试用）
/allow config        允许本会话 read_file 读取 config.yaml（审计用）
/exit                退出
"""


# 构建命令行参数解析器
def build_parser():
    parser = argparse.ArgumentParser(description="Radish AI 交互控制台")
    parser.add_argument("--debug", action="store_true", help="启动时开启调试输出")
    parser.add_argument("--verbose", action="store_true", help="启动时显示普通日志")
    parser.add_argument("--build-graph", action="store_true", help="启动时为当前目录构建代码图索引")
    return parser


def _reconfigure_stdio():
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _colorize_status(msg: str) -> str:
    lower_msg = msg.lower()
    if any(token in lower_msg for token in ("[error]", "error", "失败", "异常", "错误")):
        return f"\033[91m{msg}\033[0m"
    if any(token in lower_msg for token in ("[warn]", "warn", "警告", "注意")):
        return f"\033[93m{msg}\033[0m"
    if any(token in lower_msg for token in ("重试", "处理中", "正在", "调用工具", "工具", "loading")):
        return f"\033[96m{msg}\033[0m"
    return f"\033[90m{msg}\033[0m"


def _mode_label(mode: str) -> str:
    color_map = {
        "ask": "\033[92m",
        "plan": "\033[93m",
        "agent": "\033[95m",
        "auto": "\033[96m",
    }
    color = color_map.get(mode, "\033[95m")
    return f"{color}{mode}\033[0m"


COMMANDS = [
    "/help", "/clear", "/mode", "/mode ask", "/mode plan", "/mode agent", "/mode auto",
    "/budget", "/budget rounds", "/budget per_round", "/budget reset",
    "/debug on", "/debug off",
    "/usage on", "/usage off",
    "/setup", "/setup refresh",
    "/switch",
    "/graph", "/graph status", "/graph build", "/graph gate clear",
    "/allow config",
    "/exit",
]


if Completer is not None:

    class CommandCompleter(Completer):
        """当输入以 / 开头时，匹配并补全命令。"""

        def __init__(self, commands: list[str]):
            self.commands = commands

        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            if not text.startswith("/"):
                return
            # 只补全第一个单词（不补全参数）
            word = text.split()[-1] if text else ""
            for cmd in self.commands:
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text))


def _fmt_tokens(n: int) -> str:
    """将 token 数格式化为可读的 K/M 单位。"""
    if n >= 1_000_000:
        whole = n // 1_000_000
        frac = (n % 1_000_000) // 100_000
        if frac:
            return f"{whole}.{frac}M"
        return f"{whole}M"
    if n >= 1_000:
        return f"{n // 1_000}K"
    return str(n)


def _format_graph_status(status: dict) -> str:
    project = status.get("project_path", os.getcwd())
    if status.get("loaded"):
        return (
            f"代码图: 已加载 {status.get('node_count', 0)} nodes / "
            f"{status.get('edge_count', 0)} edges "
            f"({status.get('parser_backend', '')}) | 项目: {project}"
        )
    hint = status.get("error") or "未索引"
    return f"代码图: {hint} | 项目: {project} | 请执行 /graph build"


def _print_graph_status(bot):
    status = bot.get_code_graph_status()
    print(_format_graph_status(status))
    if status.get("path"):
        print(f"  索引路径: {status['path']}")


def _handle_graph_command(bot, user_input: str) -> bool:
    """处理 /graph 子命令，返回 True 表示已消费输入。"""
    parts = user_input.split()
    if len(parts) == 1 or (len(parts) == 2 and parts[1] == "status"):
        _print_graph_status(bot)
        return True

    if len(parts) >= 2 and parts[1] == "build":
        print("正在构建代码图索引...")
        t0 = time.time()
        try:
            status = bot.build_code_graph()
            elapsed = status.get("elapsed_sec", round(time.time() - t0, 2))
            print(
                f"构建完成: {status.get('node_count', 0)} nodes, "
                f"{status.get('edge_count', 0)} edges, 耗时 {elapsed}s"
            )
            if status.get("path"):
                print(f"  已写入: {status['path']}")
        except Exception as err:
            print(f"构建失败: {err}")
        return True

    if len(parts) >= 3 and parts[1] == "gate" and parts[2] == "clear":
        bot.clear_symbol_read_gate()
        print("符号读取门禁已清空。")
        return True

    print("用法: /graph | /graph status | /graph build | /graph gate clear")
    return True


def _build_prompt(bot) -> str:
    mode = _mode_label(bot.get_mode())
    debug = "-debug" if bot.debug else ""
    usage = f"-[{round(bot.metrics_totals['total_tokens']/1000, 1)}k]" if bot.show_usage else ""
    return f"({mode}{debug}{usage}) Radish Agent > "


def main():
    _reconfigure_stdio()
    args = build_parser().parse_args()
    last_status = {"message": ""}

    def on_status(msg: str):
        # 非重复刷屏：同样状态只提示一次
        if msg == last_status["message"]:
            return
        last_status["message"] = msg
        print(_colorize_status(msg))

    bot = Polling(verbose=args.verbose, debug=args.debug, status_callback=on_status)
    bot.refresh_code_graph(project_path=os.getcwd())

    if args.build_graph:
        print("启动参数 --build-graph：正在构建代码图...")
        try:
            bot.build_code_graph()
        except Exception as err:
            print(f"代码图构建失败: {err}")

    print("{:-^130}".format("\033[94m Radish AI Console \033[0m"))

    # 模拟加载过程，展示彩色进度条
    for i in range(101):
        bar = "█" * i + "░" * (100 - i)
        print(f"\r\033[90m加载中: [{bar}] {i}%\033[0m", end="")
        time.sleep(0.01)

    print(f"\r\033[92m加载完成: [{bar}] {i}%\033[0m", end="")
    time.sleep(0.5)
    print("\r", end='')  # 清除加载进度条

    # print(f"{'-' * 10} Radish AI Console {'-' * 10}")
    # 输出粉色的欢迎信息，提示用户输入 /help 查看命令
    print("{:^130}".format(f"\033[96m {bot.model} {_fmt_tokens(bot.get_max_token())} \033[0m") )
    print("{:^120}".format("\033[95mCiallo~ 输入 /help 查看命令，/exit 退出。\033[0m") )
    print("{:^120}".format(f"\033[90m{_format_graph_status(bot.get_code_graph_status())}\033[0m"))
    print("-" * 121)

    prompt_session = None
    if PromptSession is not None and InMemoryHistory is not None:
        prompt_session = PromptSession(
            history=InMemoryHistory(),
            completer=CommandCompleter(COMMANDS) if Completer is not None else None,
            complete_while_typing=True,
        )

    with patch_stdout():
        while True:
            try:
                prompt = _build_prompt(bot)
                if prompt_session is not None:
                    user_input = prompt_session.prompt(ANSI(prompt)).strip()
                else:
                    user_input = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                break

            if not user_input:
                continue

            if user_input == "/exit":
                print("Bye. see you next time!")
                break

            if user_input == "/help":
                print(HELP_TEXT)
                continue

            if user_input.startswith("/mode"):
                parts = user_input.split()
                if len(parts) == 1:
                    print(f"当前模式: {bot.get_mode()}")
                    continue

                if len(parts) == 2:
                    try:
                        value = bot.set_mode(parts[1])
                        print(f"模式已切换为: {value}")
                    except Exception as err:
                        print(f"模式切换失败: {err}")
                else:
                    print("用法: /mode 或 /mode ask|plan|agent|auto")
                continue

            if user_input.startswith("/budget"):
                parts = user_input.split()
                if len(parts) == 1:
                    budget = bot.get_tool_budget()
                    print(
                        "当前预算: "
                        f"max_tools_per_round={budget['max_tools_per_round']}, "
                        f"max_tool_rounds={budget['max_tool_rounds']}"
                    )
                    print(
                        "默认预算: "
                        f"max_tools_per_round={budget['defaults']['max_tools_per_round']}, "
                        f"max_tool_rounds={budget['defaults']['max_tool_rounds']}"
                    )
                    continue

                if len(parts) == 2 and parts[1] == "reset":
                    bot.reset_tool_budget()
                    print("预算已重置为默认值。")
                    continue

                if len(parts) == 3 and parts[1] in {"rounds", "per_round"}:
                    try:
                        num = int(parts[2])
                        if num < 1:
                            raise ValueError("必须为正整数")
                        if parts[1] == "rounds":
                            bot.set_tool_budget(max_tool_rounds=num)
                        else:
                            bot.set_tool_budget(max_tools_per_round=num)
                        print("预算已更新。")
                    except Exception as err:
                        print(f"预算设置失败: {err}")
                    continue
                print("用法: /budget | /budget rounds N | /budget per_round N | /budget reset")
                continue

            if user_input == "/clear":
                bot.clear_context()
                last_status["message"] = ""
                print("会话已清空。")
                continue

            if user_input.startswith("/debug"):
                parts = user_input.split()
                if len(parts) == 2 and parts[1] in {"on", "off"}:
                    enabled = parts[1] == "on"
                    bot.set_debug(enabled)
                    print(f"debug 已{'开启' if enabled else '关闭'}。")
                else:
                    print("用法: /debug on|off")
                continue

            if user_input.startswith("/usage"):
                parts = user_input.split()
                if len(parts) == 2 and parts[1] in {"on", "off"}:
                    enabled = parts[1] == "on"
                    bot.set_show_usage(enabled)
                    print(f"usage 已{'开启' if enabled else '关闭'}。")
                else:
                    print("用法: /usage on|off")
                continue

            if user_input.startswith("/setup"):
                parts = user_input.split()
                if len(parts) > 1 and parts[1] == "refresh":
                    from provider_setup import refresh_models_cache
                    refresh_models_cache()
                    continue
                from provider_setup import run_setup
                config_path = Path(__file__).resolve().parents[1] / "config.yaml"
                run_setup(str(config_path))
                continue

            if user_input.startswith("/switch"):
                parts = user_input.split()
                providers = bot.get_available_providers()
                if len(parts) >= 2:
                    target = parts[1]
                    if target not in providers:
                        print(f"提供商「{target}」未配置。可用: {', '.join(providers)}")
                        continue
                else:
                    if not providers:
                        print("没有已配置的提供商，请先使用 /setup 添加。")
                        continue
                    print("可用提供商:")
                    for i, p in enumerate(providers, 1):
                        print(f"  {i}. {p}")
                    try:
                        choice = input(f"请选择 (1-{len(providers)}): ").strip()
                        target = providers[int(choice) - 1]
                    except (ValueError, IndexError):
                        print("无效选择。")
                        continue
                try:
                    bot.switch_provider(target)
                except ValueError as err:
                    print(f"切换失败: {err}")
                    continue
                print(f"已切换到: {target} ({bot.model})")
                continue

            if user_input == "/graph" or user_input.startswith("/graph "):
                _handle_graph_command(bot, user_input)
                continue

            if user_input == "/allow config":
                bot.add_read_file_allowlist("config.yaml", "config.yml")
                print("已允许本会话 read_file 读取 config.yaml / config.yml。")
                continue

            try:
                reply = bot.sendinfo(user_input, temperature=0.2, max_tokens=3600)
                last_status["message"] = ""
                print(reply)
            except Exception as err:
                print(f"[error] {err}")
                if bot.debug:
                    traceback.print_exc()


if __name__ == "__main__":
    main()

