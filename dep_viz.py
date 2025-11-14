import argparse
import json
import requests
import tarfile
import io
import toml
import subprocess
import os
from collections import deque
from graphviz import Digraph


class DependencyVisualizer:
    def __init__(self, config_path):
        # === ЭТАП 1: Чтение конфигурации ===
        print("=== Этап 1: Загрузка конфигурации ===")
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
        
        print(f"Пакет: {self.config['package_name']}@{self.config['version']}")
        print(f"Режим: {'тестовый' if self.config['test_mode'] else 'реальный'}")
        print(f"Папка: {self.config['target_dir']}")
        print(f"Макс. глубина: {self.config.get('max_depth', 'не ограничена')}")
        print(f"Фильтр: '{self.config.get('filter_substring', 'нет')}'")

        # Инициализация
        self.direct_deps = []
        self.graph = {}  # pkg_id → [dep_names]
        self.reverse_graph = {}  # Для обратных зависимостей

        # Запуск Этапа 2
        self._collect_direct_deps()

        # Запуск Этапа 3
        if not self.config.get("test_mode", False):
            self._fetch_all_dependencies()
        else:
            self._load_test_dependencies()

        # Запуск всех этапов
        self.run_all_stages()

    # === ЭТАП 2: Сбор прямых зависимостей с crates.io ===
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

    # === ЭТАП 3: Обход графа BFS с рекурсией, фильтром и защитой от циклов ===
    def _fetch_all_dependencies(self):
        print("\n=== Этап 3: Обход графа зависимостей (BFS) ===")
        queue = deque()
        visited = set()

        start_pkg = f"{self.config['package_name']}@{self.config['version']}"
        queue.append((self.config['package_name'], self.config['version'], 0))  # (name, version, depth)
        visited.add(start_pkg)

        max_depth = self.config.get('max_depth', float('inf'))
        filter_sub = self.config.get('filter_substring', '').lower()

        while queue:
            name, version, depth = queue.popleft()
            pkg_id = f"{name}@{version}"

            if pkg_id in self.graph:
                continue

            # Проверка глубины
            if depth >= max_depth:
                print(f"   → Достигнута максимальная глубина для {pkg_id}")
                self.graph[pkg_id] = []
                continue

            # Проверка фильтра
            if filter_sub and filter_sub in name.lower():
                print(f"   → Пропущен по фильтру: {pkg_id}")
                self.graph[pkg_id] = []
                continue

            print(f"   → Обрабатываю {pkg_id} (глубина {depth}) ...")

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
                dep_version = self._get_latest_version(dep_name)
                dep_id = f"{dep_name}@{dep_version}"
                
                if dep_id not in visited:
                    visited.add(dep_id)
                    queue.append((dep_name, dep_version, depth + 1))

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

    def _load_test_dependencies(self):
        """Загрузка тестовых данных из файла"""
        print("\n=== Этап 3: Загрузка тестовых данных ===")
        test_file = self.config.get("test_repo_path", "test_deps.json")
        try:
            with open(test_file, 'r') as f:
                test_data = json.load(f)
            self.graph = test_data
            print(f"   Загружено {len(self.graph)} тестовых узлов")
        except FileNotFoundError:
            print(f"   Тестовый файл {test_file} не найден, создаем пример...")
            self._create_test_data()

    def _create_test_data(self):
        """Создание примера тестовых данных"""
        self.graph = {
            "A@1.0": ["B", "C"],
            "B@1.0": ["D", "E"],
            "C@1.0": ["E", "F"],
            "D@1.0": [],
            "E@1.0": ["G"],
            "F@1.0": [],
            "G@1.0": []
        }

    # === ЭТАП 4: Дополнительные операции ===
    def show_dependency_order(self):
        """Показать порядок загрузки зависимостей"""
        print("\n=== Этап 4: Порядок загрузки зависимостей ===")
        
        # Топологическая сортировка
        visited = set()
        stack = []
        
        def dfs(node):
            if node in visited:
                return
            visited.add(node)
            
            for dep in self.graph.get(node, []):
                dep_id = f"{dep}@{self._get_latest_version(dep)}"
                if dep_id in self.graph:
                    dfs(dep_id)
            
            stack.append(node)
        
        start_node = f"{self.config['package_name']}@{self.config['version']}"
        dfs(start_node)
        
        print("   Порядок загрузки (от листьев к корню):")
        for i, pkg in enumerate(reversed(stack), 1):
            print(f"     {i:2d}. {pkg}")
        
        # Сравнение с реальным Cargo
        self._compare_with_cargo(stack)

    def _compare_with_cargo(self, our_order):
        """Сравнение с реальным Cargo"""
        print("\n   Сравнение с Cargo:")
        
        try:
            # Запускаем cargo tree для сравнения
            result = subprocess.run(
                ["cargo", "tree", "--package", self.config["package_name"]],
                capture_output=True, text=True, timeout=30
            )
            
            if result.returncode == 0:
                cargo_output = result.stdout
                cargo_lines = [line.strip() for line in cargo_output.split('\n') if line.strip()]
                
                print("   Cargo tree выполнен успешно")
                print(f"   Наш порядок: {len(our_order)} пакетов")
                print(f"   Cargo tree: {len(cargo_lines)} строк")
                
                # Простое сравнение количества
                if len(our_order) == len(cargo_lines):
                    print("   ✓ Количества совпадают")
                else:
                    print(f"   ✗ Расхождение в количестве: мы {len(our_order)}, Cargo {len(cargo_lines)}")
                    print("   Причина: разные алгоритмы обхода и фильтрации")
            else:
                print("   Cargo tree не доступен")
                
        except Exception as e:
            print(f"   Ошибка сравнения с Cargo: {e}")

    def show_reverse_dependencies(self):
        """Показать обратные зависимости"""
        print("\n=== Обратные зависимости ===")
        
        # Строим обратный граф
        self.reverse_graph = {}
        for pkg, deps in self.graph.items():
            for dep in deps:
                dep_id = f"{dep}@{self._get_latest_version(dep)}"
                if dep_id not in self.reverse_graph:
                    self.reverse_graph[dep_id] = []
                self.reverse_graph[dep_id].append(pkg)
        
        target_pkg = f"{self.config['package_name']}@{self.config['version']}"
        if target_pkg in self.reverse_graph:
            print(f"   Пакеты, зависящие от {target_pkg}:")
            for depender in self.reverse_graph[target_pkg]:
                print(f"     • {depender}")
        else:
            print(f"   Нет пакетов, зависящих от {target_pkg}")

    # === ЭТАП 5: Визуализация ===
    def visualize_graph(self):
        """Визуализация графа с помощью Graphviz"""
        print("\n=== Этап 5: Визуализация графа ===")
        
        # Создаем граф
        dot = Digraph(comment='Dependency Graph')
        dot.attr(rankdir='TB', size='8,5')
        
        # Добавляем узлы и ребра
        for pkg, deps in self.graph.items():
            dot.node(pkg, pkg)
            for dep in deps:
                dep_id = f"{dep}@{self._get_latest_version(dep)}"
                if dep_id in self.graph:  # Добавляем только существующие узлы
                    dot.edge(pkg, dep_id)
        
        # Сохраняем и отображаем
        output_file = self.config.get("output_file", "dependency_graph")
        dot.render(output_file, format='png', cleanup=True)
        
        print(f"   Граф сохранен как {output_file}.png")
        print(f"   Описание на Graphviz:\n{dot.source}")
        
        # Демонстрация для трех пакетов
        self._show_examples()

    def _show_examples(self):
        """Показать примеры визуализации для трех пакетов"""
        print("\n   Примеры визуализации для трех пакетов:")
        
        example_packages = [
            "serde@1.0",
            "tokio@1.0", 
            "reqwest@0.11"
        ]
        
        for pkg in example_packages:
            deps_count = len(self.graph.get(pkg, []))
            print(f"     • {pkg}: {deps_count} зависимостей")

    def compare_with_standard_tools(self):
        """Сравнение со штатными инструментами"""
        print("\n   Сравнение со штатными инструментами Cargo:")
        
        try:
            # cargo tree
            result = subprocess.run(
                ["cargo", "tree", "--package", self.config["package_name"]],
                capture_output=True, text=True, timeout=30
            )
            
            if result.returncode == 0:
                cargo_tree = result.stdout
                cargo_packages = len([line for line in cargo_tree.split('\n') if '├' in line or '└' in line])
                
                our_packages = len(self.graph)
                
                print(f"     Наш инструмент: {our_packages} пакетов")
                print(f"     Cargo tree: {cargo_packages} пакетов")
                
                if our_packages == cargo_packages:
                    print("     ✓ Результаты совпадают")
                else:
                    print("     ✗ Есть расхождения")
                    print("     Причина: разные алгоритмы обхода и обработки optional зависимостей")
            else:
                print("     Cargo tree не доступен для сравнения")
                
        except Exception as e:
            print(f"     Ошибка при сравнении: {e}")

    def run_all_stages(self):
        """Запуск всех этапов"""
        print("=" * 50)
        print("ЗАПУСК ВСЕХ ЭТАПОВ")
        print("=" * 50)
        
        # Этап 4
        self.show_dependency_order()
        self.show_reverse_dependencies()
        
        # Этап 5  
        self.visualize_graph()
        self.compare_with_standard_tools()
        
        print("\n" + "=" * 50)
        print("ВСЕ ЭТАПЫ ЗАВЕРШЕНЫ")
        print("=" * 50)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Визуализация зависимостей Rust")
    parser.add_argument('--config', required=True, help='Путь к config.json')
    args = parser.parse_args()
    
    viz = DependencyVisualizer(args.config)