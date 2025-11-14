import argparse
import json
import requests
import tarfile
import io
import toml
from collections import deque


class DependencyVisualizer:
    def __init__(self, config_path):
        # === ЭТАП 1: Чтение конфигурации ===
        print("=== Этап 1: Загрузка конфигурации ===")
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
        
        print(f"Пакет: {self.config['package_name']}@{self.config['version']}")
        print(f"Режим: {'тестовый' if self.config['test_mode'] else 'реальный'}")
        print(f"Папка: {self.config['target_dir']}")

        # Инициализация
        self.direct_deps = []
        self.graph = {}  # pkg_id → [dep_names]

        # Запуск Этапа 2
        self._collect_direct_deps()

        # Запуск Этапа 3
        if not self.config.get("test_mode", False):
            self._fetch_all_dependencies()


    # ===ЭТАП 2: Сбор прямых зависимостей с crates.io===
    def _download_crate(self, name: str, version: str) -> bytes:
        url = f"https://crates.io/api/v1/crates/{name}/{version}/download"
        print(f"   → Скачиваю {name}@{version} …")
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return r.content

    def _extract_toml(self, crate_bytes: bytes) -> str:
        with tarfile.open(fileobj=io.BytesIO(crate_bytes), mode="r:gz") as tar:
            for member in tar.getmembers():
                if member.name.endswith("Cargo.toml"):
                    f = tar.extractfile(member)
                    if f:
                        return f.read().decode("utf-8")
        raise FileNotFoundError("Cargo.toml не найден")

    def _parse_dependencies(self, toml_text: str) -> list[str]:
        data = toml.loads(toml_text)
        deps = set()

        # Обычные
        if "dependencies" in data:
            for name, info in data["dependencies"].items():
                if isinstance(info, dict) and info.get("optional"):
                    continue  # Пропускаем optional
                deps.add(name)

        # Dev
        if "dev-dependencies" in data:
            for name, info in data["dev-dependencies"].items():
                if isinstance(info, dict) and info.get("optional"):
                    continue
                deps.add(name)

        # Target-specific
        if "target" in data:
            for target in data["target"].values():
                if isinstance(target, dict) and "dependencies" in target:
                    for name, info in target["dependencies"].items():
                        if isinstance(info, dict) and info.get("optional"):
                            continue
                        deps.add(name)

        return list(deps)

    def _collect_direct_deps(self):
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


    # ===ЭТАП 3: Обход графа BFS с рекурсией, фильтром и защитой от циклов===
    def _fetch_all_dependencies(self):
        print("\n=== Этап 3: Обход графа зависимостей (BFS) ===")
        queue = deque()
        visited = set()

        start_pkg = f"{self.config['package_name']}@{self.config['version']}"
        queue.append((self.config['package_name'], self.config['version']))
        visited.add(start_pkg)

        while queue:
            name, version = queue.popleft()
            pkg_id = f"{name}@{version}"

            if pkg_id in self.graph:
                continue

            print(f"   → Обрабатываю {pkg_id} ...")

            try:
                crate_bytes = self._download_crate(name, version)
                toml_text = self._extract_toml(crate_bytes)
                deps = self._parse_dependencies(toml_text)
            except Exception as e:
                print(f"   Ошибка: {e}")
                deps = []

            self.graph[pkg_id] = deps
            print(f"     └── {len(deps)} зависимостей")

            # Добавляем в очередь (защита от циклов)
            for dep_name in deps:
                # Заглушка
                dep_version = self._get_latest_version(dep_name)
                dep_id = f"{dep_name}@{dep_version}"
                if dep_id not in visited:
                    visited.add(dep_id)
                    queue.append((dep_name, dep_version))

        print(f"   Готово! Всего узлов: {len(self.graph)}")

    def _get_latest_version(self, name: str) -> str:
        """Получает последнюю версию с crates.io API."""
        try:
            url = f"https://crates.io/api/v1/crates/{name}"
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            data = r.json()
            return data["crate"]["max_version"]
        except:
            return "1.0.0"  # fallback

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Визуализация зависимостей Rust")
    parser.add_argument('--config', required=True, help='Путь к config.json')
    args = parser.parse_args()
    
    viz = DependencyVisualizer(args.config)