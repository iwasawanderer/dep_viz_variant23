import argparse
import json
import requests
import tarfile
import io
import toml


class DependencyVisualizer:
    def __init__(self, config_path):
        # === ЭТАП 1: Чтение конфигурации ===
        print("=== Этап 1: Загрузка конфигурации ===")
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
        
        print(f"Пакет: {self.config['package_name']}@{self.config['version']}")
        print(f"Режим: {'тестовый' if self.config['test_mode'] else 'реальный'}")
        print(f"Папка: {self.config['target_dir']}")

        # Инициализация для следующих этапов
        self.direct_deps = []
        self._collect_direct_deps()  # Запуск Этапа 2

    # === ЭТАП 2: Сбор прямых зависимостей с crates.io ===
    def _download_crate(self, name: str, version: str) -> bytes:
        """Скачивает .crate (tar.gz) и возвращает байты."""
        url = f"https://crates.io/api/v1/crates/{name}/{version}/download"
        print(f"   → Скачиваю {name}@{version} …")
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return r.content

    def _extract_toml(self, crate_bytes: bytes) -> str:
        """Распаковывает .tar.gz и возвращает Cargo.toml."""
        with tarfile.open(fileobj=io.BytesIO(crate_bytes), mode="r:gz") as tar:
            for member in tar.getmembers():
                if member.name.endswith("Cargo.toml"):
                    f = tar.extractfile(member)
                    if f:
                        return f.read().decode("utf-8")
        raise FileNotFoundError("Cargo.toml не найден в .crate")

    def _parse_dependencies(self, toml_text: str) -> list[str]:
        """Извлекает имена всех прямых зависимостей."""
        data = toml.loads(toml_text)
        deps = set()

        # Обычные зависимости
        if "dependencies" in data:
            deps.update(data["dependencies"].keys())
        # Dev-dependencies
        if "dev-dependencies" in data:
            deps.update(data["dev-dependencies"].keys())
        # Target-specific
        if "target" in data:
            for target in data["target"].values():
                if isinstance(target, dict) and "dependencies" in target:
                    deps.update(target["dependencies"].keys())

        return list(deps)

    def _collect_direct_deps(self):
        """Основная логика Этапа 2."""
        print("\n=== Этап 2: Сбор прямых зависимостей ===")
        name = self.config["package_name"]
        ver = self.config["version"]

        try:
            crate_bytes = self._download_crate(name, ver)
            toml_text = self._extract_toml(crate_bytes)
            deps = self._parse_dependencies(toml_text)

            print(f"   Пакет {name}@{ver} → {len(deps)} прямых зависимостей")
            for d in deps:
                print(f"     • {d}")

            self.direct_deps = deps
        except Exception as e:
            print(f"   Ошибка: {e}")
            self.direct_deps = []


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Визуализация зависимостей Rust")
    parser.add_argument('--config', required=True, help='Путь к config.json')
    args = parser.parse_args()
    
    viz = DependencyVisualizer(args.config)