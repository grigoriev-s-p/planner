# Запуск проекта

## Установка зависимостей

Из корня проекта выполните:

```powershell
python -m pip install -r requirements.txt
```

## Preprocessing датасета

Поместите исходный Excel-файл в каталог:

```text
data/raw/
```

В каталоге должен находиться ровно один файл с расширением `.xlsx` или `.xlsm`.

Запустите preprocessing из корня проекта:

```powershell
python scripts/prepare_dataset.py
```

Нормализованные CSV-файлы будут созданы в:

```text
data/processed/
```

Если исходный файл и результаты не изменились, повторный запуск завершится без
перезаписи данных. Для принудительной пересборки используйте:

```powershell
python scripts/prepare_dataset.py --force
```

## DEV UI

DEV UI — временный read only интерфейс для просмотра обработанных данных. Перед
его запуском выполните preprocessing.

Запуск:

```powershell
streamlit run dev_ui/app.py
```

Если команда `streamlit` недоступна в `PATH`, используйте:

```powershell
python -m streamlit run dev_ui/app.py
```

После запуска откройте адрес, который Streamlit выведет в терминале. Обычно это:

```text
http://localhost:8501
```
