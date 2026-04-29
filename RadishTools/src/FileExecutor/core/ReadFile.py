from os import path
from pathlib import Path

ReadFile_title = "readFileExecutor - 读取文件工具"
ReadFile_docs = "read_file工具可以读取指定文件的内容，参数是一个字符串，表示要读取的文件路径，还可以指定可选参数start_line和end_line来读取文件的部分内容(不填写则默认获取全部),line_number是一个布尔值参数，设置给出内容是否包含行号，默认为False（当需要修改代码的时候可以在读取时候设置为True），例如：<tools>read_file(\'./main.py\', start_line=1, end_line=10, line_number=True)</tools>"

class readFileExecutor:
    def __init__(self, file_path, encoding='utf-8', start_line=None, end_line=None, line_number=False):
        self.file_path = file_path
        self.encoding = encoding
        self.start_line = start_line if start_line is not None else 1
        self.end_line = end_line if end_line is not None else -1
        self.line_number = line_number

        # 参数验证
        if self.file_path is None or path.isfile(self.file_path) == False:
            raise ValueError(f"{file_path} is not a valid file path.")


    def execute(self):
        '''读取部分分行的文件内容'''
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                content = f.readlines()[self.start_line - 1:self.end_line]
                # 增加行号，方便定位问题
                if self.line_number:
                    line_content = '\n'.join([f'{i}: {line.strip()}' for i, line in enumerate(content, start=self.start_line)])
                    return line_content

            return content
        except Exception as e:
            return f"Error reading file: {e}"

if __name__ == "__main__":
    executor = readFileExecutor(file_path='./main.py')
    result = executor.execute()
    print(result)