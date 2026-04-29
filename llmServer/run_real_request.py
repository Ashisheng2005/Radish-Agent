from llmServer.llmPolling import Polling

if __name__ == '__main__':
    p = Polling(verbose=True, debug=False)
    prompt = "请用中文简短介绍 RadishWeixinBot 项目，并给出三点改进建议。"
    try:
        reply = p.sendinfo(prompt, temperature=0.2, max_tokens=300)
        print('----MODEL REPLY----')
        print(reply)
    except Exception as e:
        print('Error during sendinfo:', e)

    # 打印 metrics 文件最近 10 行（如果存在）
    try:
        path = p.metrics_file
        print('\n----METRICS FILE:', path, '----')
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.read().splitlines()
            for line in lines[-10:]:
                print(line)
    except Exception as e:
        print('Could not read metrics file:', e)
