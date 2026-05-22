"""CLI：构建项目 CODE_GRAPH.json。"""

import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="构建项目级代码图索引")
    parser.add_argument("project_path", nargs="?", default=".", help="项目根目录")
    parser.add_argument("--wiki-root", default="", help="wiki 输出根目录，默认 <project>/wiki")
    args = parser.parse_args(argv)

    from .indexer import CodeGraphIndexer
    from .store import CodeGraphStore

    wiki_root = args.wiki_root or None
    indexer = CodeGraphIndexer(args.project_path, wiki_root=wiki_root)
    index = indexer.execute()
    print(f"CODE_GRAPH saved: {indexer.store.graph_path}")
    print(f"nodes={index.stats.get('node_count', 0)} edges={index.stats.get('edge_count', 0)} backend={index.parser_backend}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
