#!/usr/bin/env python
# _*_ coding: utf-8 _*_
# @Time : 2026/1/14 下午7:32 
# @Author : Repork
# @Version：V 1.0
# @File : executor.py
# @desc :

"""
radishCMD的核心执行器
"""

import subprocess
import threading
import queue
import time
import os
import signal
import sys
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Callable, Union, List, Dict, Any
from datetime import datetime

cmd_title = "CMDExecutor - 命令行执行器组件"
cmd_docs = "cmd工具可以执行命令行指令，参数是一个字符串，表示要执行的命令，例如：<tools>cmd('ls -la')</tools>"

class CommandStatus(Enum):
    """命令执行转台枚举"""

    NOT_STARTED = "not_started"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TERMINATED = "terminated"
    TIMEOUT = "timeout"


@dataclass
class CommandResult:
    """命令执行结果"""

    status: CommandStatus
    return_code: Optional[int] = None
    output: str = ""
    error: str = ""
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    execution_id: str = ""
    pid: Optional[int] = None


class CMDExecutor:
    """
    CMD执行器组件，
    提供执行命令的能力，支持持久会话和异步操作
    """

    def __init__(self,
                 shell: bool = True,
                 encoding: str = 'utf-8',
                 work_dir: Optional[str] = None,
                 env: Optional[str] = None,
                 timeout: Optional[float] = None,
                 max_output_size: int = 10000
                 ):
        """
        初始化CMD执行器

        Args:
            shell: 是否使用shell执行命令
            encoding: 输出编码
            work_dir: 工作目录
            env: 环境变量
            timeout: 默认超时时间（秒）
            max_output_size: 最大输出大小（字节）
        """

        self.shell = shell
        self.shell_persistence = True
        self.encoding = encoding
        self.work_dir = work_dir or os.getcwd()
        self.env = env or os.environ.copy()
        self.default_timeout = timeout
        self.max_output_size = max_output_size

        self._process: Optional[subprocess.Popen] = None
        self._result_queue: queue.Queue = queue.Queue()
        self._output_buffer: List[str] = []
        self._error_buffer: List[str] = []
        self._execution_thread: Optional[threading.Thread] = None
        self._is_running = False
        self._execution_id: Optional[str] = None
        self._start_time: Optional[datetime] = None
        self._callbacks: Dict[str, List[Callable]] = {
            'on_start': [],
            'on_output': [],
            'on_complete': [],
            'on_error': []
        }

        # 保留一个持久对象
        self.persistence_process = subprocess.Popen(
                ['cmd.exe' if os.name == 'nt' else 'bash'],
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                text=True,
                bufsize=1
            )

    def initialize(self, session_id: Optional[str] = None) -> bool:
        """
        初始化CMD会话
        :param session_id: 会话id，用于标识不同的执行会话
        :return:
        """

        try:
            # 如果已有会话在运行，则关闭
            if self._is_running and self._process:
                self._cleanup()

            self._execution_id = session_id or f"cmd_{int(time.time())}"
            self._is_running = False
            self._output_buffer.clear()
            self._error_buffer.clear()

            return True

        except Exception as e:
            self._trigger_callback('on_error', str(e))
            return False

    def execute_command(self,
                        command: Union[str, List[str]],
                        timeout: Optional[float] = None,
                        wait: bool = True,
                        capture_output: bool = True
                        ) -> CommandResult:
        """
        执行命令

        :param command: 具体命令
        :param timeout: 超时时间
        :param wait: 是否等待命令执行完成
        :param capture_output: 是否捕获输出
        :return:
        """

        result = CommandResult(
            status=CommandStatus.NOT_STARTED,
            execution_id=self._execution_id,
            start_time=datetime.now()
        )

        try:
            # 触发开始回调
            self._trigger_callback('on_start', command)

            # 设置超时
            actual_timeout = timeout or self.default_timeout

            # 执行
            if wait:
                result = self._execute_sync(command, actual_timeout, capture_output)
            else:
                result = self._execute_async(command, capture_output)

        except subprocess.TimeoutExpired:
            result.status = CommandStatus.TIMEOUT
            result.end_time = datetime.now()
            self.terminate()

        except Exception as e:
            result.status = CommandStatus.FAILED
            result.error = str(e)
            result.end_time = datetime.now()

        finally:
            if result.status in [CommandStatus.SUCCESS, CommandStatus.FAILED,
                                 CommandStatus.TIMEOUT, CommandStatus.TERMINATED]:
                self._is_running = False

            # 触发完成回调
            self._trigger_callback('on_complete', result)

        return result

    def get_status(self) -> Dict[str, Any]:
        """
        获取当前执行状态
        :return:
        """

        status_info = {
            'is_running': self._is_running,
            'execution_id': self._execution_id,
            'start_time': self._start_time,
            'process_pid': self._process.pid if self._process else None,
            'output_length': len(''.join(self._output_buffer)),
            'error_length': len(''.join(self._error_buffer)),
            'buffer_size': len(self._output_buffer)
        }

        if self._process and self._process.poll() is not None:
            status_info['return_code'] = self._process.returncode
            status_info['status'] = CommandStatus.SUCCESS if self._process.returncode == 0 else CommandStatus.FAILED
        else:
            status_info['status'] = CommandStatus.RUNNING if self._is_running else CommandStatus.NOT_STARTED

        return status_info

    def get_output(self, clear_buffer: bool = False) -> str:
        """
        获取输出内容

        Args:
            clear_buffer: 是否清空缓冲区

        Returns:
            str: 输出内容
        """
        output = ''.join(self._output_buffer)
        if clear_buffer:
            self._output_buffer.clear()
        return output

    def get_error(self, clear_buffer: bool = False) -> str:
        """
        获取错误内容

        Args:
            clear_buffer: 是否清空缓冲区

        Returns:
            str: 错误内容
        """
        error = ''.join(self._error_buffer)
        if clear_buffer:
            self._error_buffer.clear()
        return error

    def terminate(self, force: bool = False) -> bool:
        """
        终止当前执行的命令

        Args:
            force: 是否强制终止

        Returns:
            bool: 是否成功终止
        """
        if not self._process:
            return False

        try:
            if force:
                # 强制终止
                if sys.platform == 'win32':
                    # Windows
                    subprocess.run(['taskkill', '/F', '/T', '/PID', str(self._process.pid)])
                else:
                    # Unix/Linux/Mac
                    os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
            else:
                # 优雅终止（发送Ctrl+C）
                if sys.platform == 'win32':
                    # Windows发送Ctrl+C
                    self._process.send_signal(signal.CTRL_C_EVENT)
                else:
                    # Unix发送SIGINT
                    self._process.send_signal(signal.SIGINT)

            # 等待进程结束
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if force:
                    self._process.kill()
                else:
                    return False

            return True

        except Exception as e:
            self._trigger_callback('on_error', f"Termination failed: {str(e)}")
            return False

    def close(self):
        """关闭执行器，清理资源"""
        self.terminate(force=True)

        if self._execution_thread and self._execution_thread.is_alive():
            self._execution_thread.join(timeout=2)

        self._cleanup()

    def _cleanup(self):
        """清理资源"""
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=1)

            except Exception as e:
                print(f"cleanup error: {e}")

        self._process = None
        self._is_running = False
        self._execution_thread = None
        self._output_buffer.clear()
        self._error_buffer.clear()

    def register_callback(self, event: str, callback: Callable):
        """
        注册事件回调

        Args:
            event: 事件类型 ('on_start', 'on_output', 'on_complete', 'on_error')
            callback: 回调函数
        """
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    def _execute_sync(self,
                      command: Union[str, List[str]],
                      timeout: Optional[float],
                      capture_output: bool
                      ) -> CommandResult:
        """
        同步执行命令

        :param command:
        :param timeout:
        :param capture_output:
        :return:
        """

        result = CommandResult(
            status=CommandStatus.RUNNING,
            execution_id=self._execution_id,
            start_time=datetime.now()
        )

        try:
            # 创建子进程
            if self.shell_persistence:
                self._process = self.persistence_process
                self._process.stdin.write(command + '\n')
                self._process.stdin.flush()

            else:

                self._process = subprocess.Popen(
                    command,
                    shell=self.shell,
                    stdout=subprocess.PIPE if capture_output else None,
                    stderr=subprocess.PIPE if capture_output else None,
                    stdin=subprocess.PIPE,
                    cwd=self.work_dir,
                    env=self.env,
                    text=True,
                    encoding=self.encoding,
                    bufsize=1
                )

            result.pid = self._process.pid
            self._is_running = True

            # 读取输出
            output, error = self._process.communicate(timeout=timeout)

            # 设置结果
            result.return_code = self._process.returncode
            result.output = output or ""
            result.error = error or ""
            result.status = CommandStatus.SUCCESS if result.return_code == 0 else CommandStatus.FAILED
            result.end_time = datetime.now()

            # 处理输出回调
            if output:
                self._output_buffer.append(output)
                self._trigger_callback('on_output', output)

        except Exception as e:
            result.status = CommandStatus.FAILED
            result.error = str(e)
            result.end_time = datetime.now()
            raise

        finally:
            self._process = None

        return result

    def _execute_async(self,
                       command: Union[str, List[str]],
                       capture_output: bool
                       ) -> CommandResult:

        """
        异步执行命令

        :param command: 命令
        :param capture_output:
        :return:
        """

        result = CommandResult(
            status=CommandStatus.RUNNING,
            execution_id=self._execution_id,
            start_time=datetime.now()
        )

        # 启动执行线程
        self._execution_thread = threading.Thread(
            target=self._execute_in_thread,
            args=(command, capture_output, result),
            daemon=True
        )

        self._execution_thread.start()
        return result

    def _execute_in_thread(self,
                           command: Union[str, List[str]],
                           capture_output: bool,
                           result: CommandResult
                           ):
        """
        在独立线程中执行

        :param command:
        :param capture_output:
        :param result:
        :return:
        """

        try:
            if self.shell_persistence:
                self._process = self.persistence_process
                self._process.stdin.write(command + '\n')
                self._process.stdin.flush()

            else:
                self._process = subprocess.Popen(
                    command,
                    shell=self.shell,
                    stdout=subprocess.PIPE if capture_output else None,
                    stderr=subprocess.PIPE if capture_output else None,
                    stdin=subprocess.PIPE,
                    cwd=self.work_dir,
                    env=self.env,
                    text=True,
                    encoding=self.encoding,
                    bufsize=1
                )

            result.pid = self._process.pid
            self._is_running = True

            # 实时读取输出
            if capture_output and self._process.stdout:
                for line in iter(self._process.stdout.readline, ''):
                    if line:
                        result.output += line
                        self._output_buffer.append(line)
                        self._trigger_callback('on_output', line)

            # 等待进程完成
            return_code = self._process.wait()
            result.return_code = return_code
            result.status = CommandStatus.SUCCESS if return_code == 0 else CommandStatus.FAILED

        except Exception as e:
            result.status = CommandStatus.FAILED
            result.error = str(e)

        finally:
            result.end_time = datetime.now()
            self._process = True
            self._is_running = False
            self._result_queue.put(result)

    def _trigger_callback(self, event: str, *args, **kwargs):
        """
        触发回调

        :param event:
        :param args:
        :param kwargs:
        :return:
        """

        if event in self._callbacks:
            for callback in self._callbacks[event]:
                try:
                    callback(*args, **kwargs)

                except Exception as e:
                    print(f"callback error: {e}")




