import argparse
import json

class DependencyVisualizer:
    def __init__(self, config_path):
        # === ЭТАП 1: Чтение конфигурации ===
        print("=== Этап 1: Загрузка конфигурации ===")
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
        
        print(f"Пакет: {self.config['package_name']}@{self.config['version']}")
        print(f"Режим: {'тестовый' if self.config['test_mode'] else 'реальный'}")
        print(f"Папка: {self.config['target_dir']}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Визуализация зависимостей Rust")
    parser.add_argument('--config', required=True, help='Путь к config.json')
    args = parser.parse_args()
    
    viz = DependencyVisualizer(args.config)