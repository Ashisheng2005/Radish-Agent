import yaml
import os

class Config:
    """加载YAML配置文件并提供对设置的访问。"""

    def __init__(self, config_path):
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"配置文件 {config_path} 不存在.")
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

    def get(self, key, default=None):
        return self.config.get(key, default)

    def get_nested(self, *keys, default=None):
        value = self.config
        for key in keys:
            if not isinstance(value, dict):
                return default
            if key not in value:
                return default
            value = value[key]
        return value