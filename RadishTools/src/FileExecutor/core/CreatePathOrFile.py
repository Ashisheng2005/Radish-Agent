from os import path
from pathlib import Path

createPathOrFile_title = "createPathOrFileExecutor - 创建文件或目录工具"
createPathOrFile_docs = "create_path_or_file工具可以创建指定的文件或目录，参数是一个字符串，表示要创建的路径，以及一个布尔值is_file来指定是创建文件还是目录，例如：<tools>create_path_or_file('./test_dir/test_file.txt', is_file=True)</tools>"

class createPathOrFileExecutor:
    def __init__(self, path, is_file=False):
        self.path = path
        self.is_file = is_file

    def execute(self):
        '''创建文件或目录'''
        try:
            if self.is_file:
                # 创建文件
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
                Path(self.path).touch(exist_ok=True)
                return f"File '{self.path}' created successfully."
            else:
                # 创建目录
                Path(self.path).mkdir(parents=True, exist_ok=True)
                return f"Directory '{self.path}' created successfully."
        except Exception as e:
            return f"Error creating path or file: {e}"
        
if __name__ == "__main__":
    executor = createPathOrFileExecutor(path='./test_dir/test_file.txt', is_file=True)
    result = executor.execute()
    print(result)