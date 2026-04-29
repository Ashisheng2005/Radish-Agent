import os
path = "/home/repork/project/RadishWeixinBot/RadishTools/src/FileExecutor/core/WriteFile.py"
with open(path, "r") as f:
    content = f.read()
# 删除第7-11行（重复import），保留第1-6行
lines = content.split("\n")
print("Total lines:", len(lines))
new_lines = lines[:6] + lines[11:]
with open(path, "w") as f:
    f.write("\n".join(new_lines))
print("Done. New total lines:", len(new_lines))
    print(f"{i}: {repr(line)}")
