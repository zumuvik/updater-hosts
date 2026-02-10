#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для генерации файла hosts из списка доменов.
Определяет IP-адреса доменов и создает файл hosts для обхода блокировок.
"""

import os
import sys
import socket
import platform
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Tuple, Optional
from threading import Lock

# Попытка импортировать dnspython (опционально)
try:
    import dns.resolver
    HAS_DNSPYTHON = True
except ImportError:
    HAS_DNSPYTHON = False

# Попытка импортировать tqdm для прогресс-бара (опционально)
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# Глобальные счетчики для прогресса
progress_lock = Lock()
progress_counter = {'success': 0, 'failed': 0, 'total': 0}


def get_available_txt_files() -> List[str]:
    """Получает список всех .txt файлов в текущей директории."""
    current_dir = Path('.')
    txt_files = [f.name for f in current_dir.glob('*.txt')]
    return sorted(txt_files)


def select_txt_file() -> str:
    """Предлагает выбрать .txt файл из доступных."""
    txt_files = get_available_txt_files()
    
    if not txt_files:
        print("❌ Не найдено ни одного .txt файла в текущей директории!")
        sys.exit(1)
    
    default_file = 'general.txt'
    
    if len(txt_files) == 1:
        print(f"📄 Найден файл: {txt_files[0]}")
        return txt_files[0]
    
    print("\n📋 Доступные .txt файлы:")
    for i, file in enumerate(txt_files, 1):
        marker = " (по умолчанию)" if file == default_file else ""
        print(f"  {i}. {file}{marker}")
    
    if default_file in txt_files:
        choice = input(f"\nВыберите файл (Enter для '{default_file}'): ").strip()
        if not choice:
            return default_file
        
        try:
            index = int(choice) - 1
            if 0 <= index < len(txt_files):
                return txt_files[index]
        except ValueError:
            pass
        
        # Если введено имя файла напрямую
        if choice in txt_files:
            return choice
    
    # Если default_file не найден, используем первый
    if default_file not in txt_files:
        print(f"⚠️  Файл '{default_file}' не найден. Используется: {txt_files[0]}")
        return txt_files[0]
    
    return default_file


def read_domains(file_path: str) -> List[str]:
    """Читает домены из файла."""
    domains = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                # Пропускаем пустые строки и комментарии
                if line and not line.startswith('#'):
                    # Убираем возможные префиксы http://, https://, www.
                    domain = line.replace('http://', '').replace('https://', '').replace('www.', '')
                    domain = domain.split('/')[0].split(':')[0].strip()
                    if domain:
                        domains.append(domain)
    except FileNotFoundError:
        print(f"❌ Файл '{file_path}' не найден!")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Ошибка при чтении файла: {e}")
        sys.exit(1)
    
    return domains


def resolve_domain(domain: str, timeout: int = 3) -> Optional[str]:
    """Резолвит IP-адрес домена с использованием нескольких методов."""
    # Метод 1: Стандартный socket с таймаутом
    try:
        # Устанавливаем таймаут для socket операций
        socket.setdefaulttimeout(timeout)
        ip = socket.gethostbyname(domain)
        return ip
    except (socket.gaierror, socket.timeout, OSError):
        pass
    
    # Метод 2: getaddrinfo с таймаутом
    try:
        socket.setdefaulttimeout(timeout)
        result = socket.getaddrinfo(domain, None, socket.AF_INET, socket.SOCK_STREAM)
        if result:
            return result[0][4][0]
    except (socket.gaierror, socket.timeout, OSError):
        pass
    
    # Метод 3: Попробуем через альтернативные DNS серверы (если доступен dnspython)
    if HAS_DNSPYTHON:
        try:
            resolver = dns.resolver.Resolver()
            resolver.timeout = timeout
            resolver.lifetime = timeout
            
            # Пробуем Google DNS
            resolver.nameservers = ['8.8.8.8', '8.8.4.4']
            answers = resolver.resolve(domain, 'A')
            if answers:
                return str(answers[0])
        except:
            pass
        
        try:
            resolver = dns.resolver.Resolver()
            resolver.timeout = timeout
            resolver.lifetime = timeout
            
            # Пробуем Cloudflare DNS
            resolver.nameservers = ['1.1.1.1', '1.0.0.1']
            answers = resolver.resolve(domain, 'A')
            if answers:
                return str(answers[0])
        except:
            pass
    
    # Метод 4: Попробуем через системный DNS с увеличенным таймаутом (только для важных доменов)
    # Пропускаем для ускорения - используем только если предыдущие методы не сработали
    # и домен выглядит важным (не тестовый)
    if timeout > 2:  # Только если таймаут достаточно большой
        try:
            socket.setdefaulttimeout(timeout * 1.5)
            ip = socket.gethostbyname(domain)
            return ip
        except:
            pass
    
    return None


def find_similar_domains(domain: str, successful_domains: dict, max_suggestions: int = 5) -> List[Tuple[str, str, str]]:
    """Находит похожие домены в успешных и возвращает их IP. Оптимизировано для больших списков."""
    suggestions = []
    domain_lower = domain.lower()
    
    # Разбиваем домен на части
    parts = domain_lower.split('.')
    if len(parts) < 2:
        return suggestions
    
    base_name = '.'.join(parts[:-1])  # Все кроме TLD
    tld = parts[-1]
    
    # Оптимизация: ограничиваем поиск первыми N доменами для скорости
    # В реальности лучше использовать индексированные структуры данных
    max_search = min(1000, len(successful_domains))  # Ограничиваем поиск
    
    # Стратегии поиска похожих доменов
    checked = 0
    for success_domain, success_ip in successful_domains.items():
        if checked >= max_search:
            break
        
        checked += 1
        success_domain_lower = success_domain.lower()
        success_parts = success_domain_lower.split('.')
        
        if len(success_parts) < 2:
            continue
        
        success_base = '.'.join(success_parts[:-1])
        success_tld = success_parts[-1]
        
        # Стратегия 1: Тот же базовый домен, другой TLD (высокий приоритет)
        if base_name == success_base and tld != success_tld:
            suggestions.insert(0, (success_domain, success_ip, 'разный TLD'))  # Добавляем в начало
            if len(suggestions) >= max_suggestions:
                return suggestions[:max_suggestions]
        
        # Стратегия 2: Похожий базовый домен (разница в 1-3 символа)
        elif base_name != success_base and len(suggestions) < max_suggestions:
            # Проверяем, начинается ли один с другого
            if (base_name.startswith(success_base) or success_base.startswith(base_name)) and \
               abs(len(base_name) - len(success_base)) <= 3:
                suggestions.append((success_domain, success_ip, 'похожее имя'))
                if len(suggestions) >= max_suggestions:
                    return suggestions[:max_suggestions]
            
            # Проверяем частичное совпадение (один домен содержит другой)
            elif abs(len(base_name) - len(success_base)) <= 5:
                if base_name in success_base or success_base in base_name:
                    suggestions.append((success_domain, success_ip, 'частичное совпадение'))
                    if len(suggestions) >= max_suggestions:
                        return suggestions[:max_suggestions]
    
    return suggestions[:max_suggestions]




def try_domain_variants(domain: str, timeout: int) -> Optional[str]:
    """Пробует резолвить варианты домена (разные TLD, без поддоменов)."""
    parts = domain.split('.')
    if len(parts) < 2:
        return None
    
    base_name = '.'.join(parts[:-1])
    original_tld = parts[-1]
    
    # Популярные TLD для попытки
    common_tlds = ['com', 'net', 'org', 'ru', 'io', 'co', 'info', 'top', 'xyz', 'site']
    
    # Пробуем резолвить с разными TLD
    for tld in common_tlds:
        if tld == original_tld:
            continue
        
        variant = f"{base_name}.{tld}"
        try:
            ip = resolve_domain(variant, timeout=1)  # Быстрый таймаут для вариантов
            if ip:
                return ip
        except:
            continue
    
    # Пробуем без поддоменов (если есть)
    if len(parts) > 2:
        # Берем только основной домен и TLD
        main_domain = f"{parts[-2]}.{parts[-1]}"
        try:
            ip = resolve_domain(main_domain, timeout=1)
            if ip:
                return ip
        except:
            pass
    
    return None


def resolve_domain_wrapper(args: Tuple[str, int, int, dict, Lock]) -> Tuple[str, Optional[str], int]:
    """Обертка для резолва домена с индексом для сохранения порядка."""
    domain, timeout, index, successful_domains, successful_lock = args
    
    # Сначала пробуем стандартный резолв
    ip = resolve_domain(domain, timeout)
    
    # Если не получилось и включен поиск похожих, пробуем найти похожий домен
    if not ip:
        # Блокируем доступ к словарю для чтения
        with successful_lock:
            # Создаем копию словаря для безопасного чтения
            successful_copy = dict(successful_domains)
        
        # Ищем похожие домены в уже успешно резолвленных
        similar = find_similar_domains(domain, successful_copy, max_suggestions=3)
        
        if similar:
            # Пробуем использовать IP похожих доменов
            for similar_domain, similar_ip, reason in similar:
                # Проверяем, что IP валидный
                try:
                    socket.inet_aton(similar_ip)
                    ip = similar_ip
                    break  # Используем первый найденный валидный IP
                except:
                    continue
        
        # Если все еще не нашли, пробуем варианты домена (разные TLD)
        if not ip:
            ip = try_domain_variants(domain, timeout=1)
    
    # Обновляем счетчики
    with progress_lock:
        progress_counter['total'] += 1
        if ip:
            progress_counter['success'] += 1
        else:
            progress_counter['failed'] += 1
    
    return (domain, ip, index)


def resolve_domains(domains: List[str], timeout: int = 3, max_workers: int = 50, 
                    use_similar_fallback: bool = True) -> List[Tuple[str, Optional[str]]]:
    """Резолвит IP-адреса для списка доменов с использованием многопоточности."""
    total = len(domains)
    
    # Определяем оптимальное количество потоков
    if max_workers is None:
        # Автоматически определяем на основе количества доменов
        if total < 100:
            max_workers = 10
        elif total < 1000:
            max_workers = 30
        else:
            max_workers = 50  # Максимум для DNS запросов
    
    print(f"\n🔍 Резолв {total} доменов...")
    print(f"   Потоков: {max_workers}, Таймаут: {timeout}с")
    if use_similar_fallback:
        print("   (Включен поиск похожих доменов для неудачных резолвов)")
    if HAS_DNSPYTHON:
        print("   (Используется расширенный режим с альтернативными DNS серверами)")
    else:
        print("   (Для лучших результатов установите: pip install dnspython)")
    if HAS_TQDM:
        print("   (Используется прогресс-бар)")
    else:
        print("   (Для прогресс-бара установите: pip install tqdm)")
    
    # Сбрасываем счетчики
    with progress_lock:
        progress_counter['success'] = 0
        progress_counter['failed'] = 0
        progress_counter['total'] = 0
    
    # Словарь успешных доменов для поиска похожих (обновляется по мере обработки)
    successful_domains = {}
    
    # Подготавливаем аргументы с индексами для сохранения порядка
    # Для первой итерации используем пустой словарь, потом обновим
    domain_args = [(domain, timeout, i, successful_domains) for i, domain in enumerate(domains)]
    
    # Создаем словарь для результатов с индексами
    results_dict = {}
    start_time = time.time()
    
    # Используем ThreadPoolExecutor для параллельной обработки
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Запускаем все задачи
        future_to_domain = {
            executor.submit(resolve_domain_wrapper, args): args[0] 
            for args in domain_args
        }
        
        # Обновляем словарь успешных доменов по мере получения результатов
        if use_similar_fallback:
            # Сначала делаем быстрый проход без fallback для накопления базы
            # Но это усложнит код, поэтому просто обновляем по мере поступления
            pass
        
        # Обрабатываем результаты по мере их поступления
        if HAS_TQDM:
            # С прогресс-баром
            with tqdm(total=total, desc="Резолв доменов", unit="домен") as pbar:
                for future in as_completed(future_to_domain):
                    domain, ip, index = future.result()
                    results_dict[index] = (domain, ip)
                    pbar.update(1)
                    
                    # Обновляем описание прогресс-бара
                    with progress_lock:
                        success = progress_counter['success']
                        failed = progress_counter['failed']
                        elapsed = time.time() - start_time
                        rate = progress_counter['total'] / elapsed if elapsed > 0 else 0
                        pbar.set_postfix({
                            '✓': success,
                            '✗': failed,
                            'скорость': f'{rate:.1f}/с'
                        })
        else:
            # Без прогресс-бара - простой вывод каждые N доменов
            completed = 0
            last_print = 0
            print_interval = max(1, total // 100)  # Печатаем каждые 1% или минимум каждый домен
            
            for future in as_completed(future_to_domain):
                domain, ip, index = future.result()
                results_dict[index] = (domain, ip)
                completed += 1
                
                # Периодически выводим прогресс
                if completed - last_print >= print_interval or completed == total:
                    elapsed = time.time() - start_time
                    rate = completed / elapsed if elapsed > 0 else 0
                    with progress_lock:
                        success = progress_counter['success']
                        failed = progress_counter['failed']
                    remaining = total - completed
                    eta = remaining / rate if rate > 0 else 0
                    print(f"  Прогресс: {completed}/{total} ({completed/total*100:.1f}%) | "
                          f"✓ {success} ✗ {failed} | "
                          f"{rate:.1f} домен/с | "
                          f"Осталось: ~{eta:.0f}с", end='\r', flush=True)
                    last_print = completed
            
            print()  # Новая строка после завершения
    
    # Сортируем результаты по индексу и возвращаем без индексов
    results = [results_dict[i] for i in range(total)]
    
    elapsed_time = time.time() - start_time
    print(f"\n✅ Резолв завершен за {elapsed_time:.1f}с "
          f"({total/elapsed_time:.1f} домен/с)")
    
    return results


def generate_hosts_file(results: List[Tuple[str, Optional[str]]]) -> str:
    """Генерирует содержимое файла hosts."""
    lines = [
        "# Файл hosts, сгенерированный автоматически",
        "# Для применения: скопируйте содержимое в /etc/hosts (Linux) или C:\\Windows\\System32\\drivers\\etc\\hosts (Windows)",
        "",
    ]
    
    successful = 0
    failed = 0
    
    for domain, ip in results:
        if ip:
            lines.append(f"{ip}\t{domain}")
            successful += 1
        else:
            lines.append(f"# {domain} - не удалось определить IP")
            failed += 1
    
    lines.append("")
    lines.append(f"# Всего обработано: {len(results)}, успешно: {successful}, ошибок: {failed}")
    
    return '\n'.join(lines)


def get_hosts_path() -> Tuple[str, str]:
    """Возвращает путь к файлу hosts в зависимости от ОС."""
    system = platform.system()
    
    if system == 'Linux':
        return '/etc/hosts', 'Linux'
    elif system == 'Windows':
        return r'C:\Windows\System32\drivers\etc\hosts', 'Windows'
    elif system == 'Darwin':  # macOS
        return '/etc/hosts', 'macOS'
    else:
        return '/etc/hosts', 'Unix-подобная'


def backup_system_hosts() -> bool:
    """Создает резервную копию текущего системного файла hosts в `hosts.backup`.

    Возвращает True при успешном создании резервной копии, иначе False.
    """
    hosts_path, os_name = get_hosts_path()
    backup_file = Path('hosts.backup')

    try:
        # Попытка прочитать системный hosts (обычно доступно для чтения)
        with open(hosts_path, 'r', encoding='utf-8') as f:
            data = f.read()
    except Exception as e:
        print(f"⚠️ Не удалось прочитать системный hosts ({hosts_path}): {e}")
        return False

    try:
        with open(backup_file, 'w', encoding='utf-8') as f:
            f.write(data)
        print(f"💾 Стартовый файл hosts сохранён в: {backup_file.absolute()}")
        return True
    except Exception as e:
        print(f"❌ Не удалось создать резервную копию hosts: {e}")
        return False


def save_hosts_file(content: str, output_file: str = 'hosts'):
    """Сохраняет файл hosts в текущую директорию."""
    try:
        output_path = Path(output_file)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"\n✅ Файл '{output_path.absolute()}' успешно создан!")
        return True
    except Exception as e:
        print(f"\n❌ Ошибка при сохранении файла: {e}")
        return False


def copy_to_system_hosts(local_file: str, system_hosts_path: str) -> bool:
    """Копирует содержимое локального файла в системный hosts."""
    try:
        local_path = Path(local_file)
        system_path = Path(system_hosts_path)
        
        if not local_path.exists():
            print(f"❌ Локальный файл '{local_file}' не найден!")
            return False
        
        # Читаем содержимое локального файла (только записи, без комментариев о генерации)
        with open(local_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # Фильтруем только реальные записи (IP + домен)
        entries = []
        for line in lines:
            line = line.strip()
            # Пропускаем комментарии и пустые строки
            if line and not line.startswith('#'):
                # Проверяем, что это запись вида "IP\tдомен" или "IP домен"
                parts = line.split()
                if len(parts) >= 2:
                    entries.append(line)
        
        if not entries:
            print("⚠️  В файле нет записей для добавления!")
            return False
        
        system = platform.system()
        
        if system == 'Linux' or system == 'Darwin':
            # Для Linux/macOS используем sudo
            print(f"\n📋 Добавление записей в {system_path}...")
            print("⚠️  Требуется ввод пароля администратора")
            
            # Создаем временный файл с новыми записями
            temp_file = Path('/tmp/hosts_entries.txt')
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(entries))
                f.write('\n')
            
            # Добавляем в системный hosts через sudo
            try:
                result = subprocess.run(
                    ['sudo', 'sh', '-c', f'cat {temp_file} >> {system_path}'],
                    check=True,
                    capture_output=True,
                    text=True
                )
                temp_file.unlink()  # Удаляем временный файл
                print(f"✅ Записи успешно добавлены в {system_path}")
                return True
            except subprocess.CalledProcessError as e:
                temp_file.unlink(missing_ok=True)
                print(f"❌ Ошибка при добавлении записей: {e.stderr}")
                return False
            except FileNotFoundError:
                print("❌ Команда 'sudo' не найдена. Добавьте записи вручную.")
                return False
        
        elif system == 'Windows':
            # Для Windows нужно запустить PowerShell от администратора
            print(f"\n📋 Добавление записей в {system_path}...")
            print("⚠️  Для Windows требуется запустить PowerShell от имени администратора")
            print("\nВыполните следующую команду в PowerShell (от администратора):")
            print(f"  Get-Content '{local_path.absolute()}' | Add-Content '{system_path}'")
            print("\nИли добавьте записи вручную:")
            print("\n".join(entries[:5]))  # Показываем первые 5 записей
            if len(entries) > 5:
                print(f"  ... и еще {len(entries) - 5} записей")
            return False
        
        else:
            print(f"⚠️  Автоматическое копирование не поддерживается для {system}")
            print(f"Добавьте содержимое файла '{local_file}' в {system_path} вручную")
            return False
            
    except Exception as e:
        print(f"❌ Ошибка при копировании: {e}")
        return False


def main():
    """Основная функция."""
    print("=" * 60)
    print("🌐 Генератор файла hosts для обхода блокировок")
    print("=" * 60)
    # Создаем резервную копию системного файла hosts перед любыми операциями
    try:
        backup_system_hosts()
    except Exception:
        # Не фатальная ошибка — продолжаем работу, но уведомим пользователя
        print("⚠️  Не удалось автоматически сохранить резервную копию hosts")
    
    # Выбор файла с доменами
    txt_file = select_txt_file()
    print(f"\n📖 Чтение доменов из файла: {txt_file}")
    
    # Чтение доменов
    domains = read_domains(txt_file)
    if not domains:
        print("❌ Не найдено ни одного домена в файле!")
        sys.exit(1)
    
    # Удаляем дубликаты, сохраняя порядок
    original_count = len(domains)
    seen = set()
    unique_domains = []
    for domain in domains:
        domain_lower = domain.lower()  # Нормализуем к нижнему регистру
        if domain_lower not in seen:
            seen.add(domain_lower)
            unique_domains.append(domain)
    
    domains = unique_domains
    duplicates_count = original_count - len(domains)
    
    print(f"✓ Найдено доменов: {original_count}")
    if duplicates_count > 0:
        print(f"✓ Удалено дубликатов: {duplicates_count}")
        print(f"✓ Уникальных доменов: {len(domains)}")
    
    # Определяем оптимальные параметры для резолва
    total_domains = len(domains)
    
    # Настройка таймаута и потоков в зависимости от количества доменов
    if total_domains < 100:
        timeout = 5
        max_workers = 10
    elif total_domains < 1000:
        timeout = 3
        max_workers = 30
    elif total_domains < 10000:
        timeout = 3
        max_workers = 50
    else:
        timeout = 2  # Уменьшаем таймаут для очень больших списков
        max_workers = 100  # Увеличиваем потоки для очень больших списков
    
    # Для очень больших файлов предлагаем настройку
    if total_domains > 10000:
        print(f"\n⚙️  Настройки производительности:")
        print(f"   Таймаут: {timeout}с, Потоков: {max_workers}")
        custom = input("   Изменить настройки? (y/n, Enter для пропуска): ").strip().lower()
        if custom in ['y', 'yes', 'д', 'да']:
            try:
                workers_input = input(f"   Количество потоков (по умолчанию {max_workers}): ").strip()
                if workers_input:
                    max_workers = int(workers_input)
                    max_workers = max(1, min(max_workers, 200))  # Ограничение 1-200
                
                timeout_input = input(f"   Таймаут в секундах (по умолчанию {timeout}): ").strip()
                if timeout_input:
                    timeout = float(timeout_input)
                    timeout = max(1, min(timeout, 10))  # Ограничение 1-10 секунд
            except ValueError:
                print("   ⚠️  Неверный ввод, используются значения по умолчанию")
    
    # Резолв доменов с поиском похожих доменов для неудачных резолвов
    results = resolve_domains(domains, timeout=timeout, max_workers=max_workers, 
                              use_similar_fallback=True)
    
    # Генерация hosts файла
    print("\n📝 Генерация файла hosts...")
    hosts_content = generate_hosts_file(results)
    
    # Сохранение файла в текущую директорию
    output_file = 'hosts'
    if not save_hosts_file(hosts_content, output_file):
        sys.exit(1)
    
    # Предложение скопировать в системную папку
    hosts_path, os_name = get_hosts_path()
    print("\n" + "=" * 60)
    print("📋 Применить файл hosts в систему?")
    print("=" * 60)
    print(f"\nФайл сохранен в: {Path(output_file).absolute()}")
    print(f"Системный файл hosts: {hosts_path}")
    
    choice = input("\nДобавить записи в системный файл hosts? (y/n): ").strip().lower()
    
    if choice in ['y', 'yes', 'д', 'да']:
        if copy_to_system_hosts(output_file, hosts_path):
            print("\n⚠️  ВАЖНО: После изменения файла hosts может потребоваться очистить DNS кэш!")
            if platform.system() == 'Linux':
                flush_choice = input("\nОчистить DNS кэш сейчас? (y/n): ").strip().lower()
                if flush_choice in ['y', 'yes', 'д', 'да']:
                    try:
                        subprocess.run(['sudo', 'systemd-resolve', '--flush-caches'], check=True)
                        print("✅ DNS кэш очищен")
                    except:
                        print("⚠️  Не удалось очистить кэш автоматически. Выполните вручную:")
                        print("  sudo systemd-resolve --flush-caches")
            elif platform.system() == 'Darwin':
                flush_choice = input("\nОчистить DNS кэш сейчас? (y/n): ").strip().lower()
                if flush_choice in ['y', 'yes', 'д', 'да']:
                    try:
                        subprocess.run(['sudo', 'dscacheutil', '-flushcache'], check=True)
                        print("✅ DNS кэш очищен")
                    except:
                        print("⚠️  Не удалось очистить кэш автоматически. Выполните вручную:")
                        print("  sudo dscacheutil -flushcache")
            elif platform.system() == 'Windows':
                print("\nДля очистки DNS кэша выполните:")
                print("  ipconfig /flushdns")
        else:
            print("\n📋 Инструкции по ручному применению:")
            print(f"  1. Откройте файл: {hosts_path}")
            print(f"  2. Добавьте содержимое файла '{Path(output_file).absolute()}' в конец файла hosts")
            print(f"  3. Сохраните файл (может потребоваться права администратора)")
    else:
        print("\n📋 Для применения вручную:")
        print(f"  Файл сохранен: {Path(output_file).absolute()}")
        print(f"  Системный файл: {hosts_path}")
        
        if platform.system() == 'Linux' or platform.system() == 'Darwin':
            print(f"\nВыполните команду:")
            print(f"  sudo cat {output_file} >> {hosts_path}")
        elif platform.system() == 'Windows':
            print(f"\nВ PowerShell (от администратора):")
            print(f"  Get-Content '{Path(output_file).absolute()}' | Add-Content '{hosts_path}'")
    
    print("\n" + "=" * 60)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Прервано пользователем")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        sys.exit(1)
