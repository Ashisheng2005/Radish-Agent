
import os

ListDir_title = "listDirExecutor - 列出目录工具"
ListDir_docs = 'list_dir工具可以列出指定目录下的文件和文件夹，参数是一个字符串，表示要列出的目录路径，例如：<tools>list_dir(\'./\')</tools>'

class listDirExecutor:
    def __init__(self, path):
        self.path = path
        self.tree = []

        # 参数验证
        if os.path.isdir(self.path) == False:
            raise ValueError(f"{path} is not a valid directory path.")

    def build_tree(self):
        '''构建目录树'''
        for item in os.listdir(self.path):
            item_path = os.path.join(self.path, item)
            if os.path.isdir(item_path):
                self.tree.append({'type': 'dir', 'path': item_path})
            else:
                self.tree.append({'type': 'file', 'path': item_path})

    def get_tree(self):
        path_content = "".join([f"typr:{item['type']},path:{item['path']}\n" for item in self.tree])
        return path_content

        # return self.tree

if __name__ == "__main__":
    executor = listDirExecutor(path='.')
    executor.build_tree()
    tree = executor.get_tree()
    print(tree)