# Garuda Analytics MVP (Data Pipeline)

Proyek ini adalah MVP pipeline data untuk mengumpulkan data harga pasar pemain dari Transfermarkt dan berita terkait dari Google News RSS, lalu menyimpan hasilnya ke CSV lokal.

## Struktur
- `src/garuda_mvp.py`: script ETL utama
- `config/players.json`: daftar pemain dan sumber data
- `output/raw/`: hasil ekstraksi per sumber
- `output/staging/`: data hasil parsing dan normalisasi awal
- `output/curated/`: dataset final untuk analisis dan quality report
- `output/garuda_mvp.log`: log eksekusi pipeline
- `output/timnas_mvp_error_report.csv`: error report untuk run terakhir

## Prasyarat
- Python 3.10+

## Instalasi
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Konfigurasi
Edit `config/players.json` untuk menambahkan pemain dan URL Transfermarkt.

## Menjalankan
```bash
python src/garuda_mvp.py
```

Output utama akan tersimpan sebagai layer data lokal:
- `output/raw/transfermarkt_market_values.csv`
- `output/raw/google_news_articles.csv`
- `output/staging/player_market_values.csv`
- `output/staging/news_articles.csv`
- `output/curated/player_daily_snapshot.csv`
- `output/curated/player_news_articles.csv`
- `output/curated/data_quality_report.csv`

## Catatan
- Scraping web dapat berubah sewaktu-waktu karena struktur HTML.
- Jika Transfermarkt memblokir, coba naikkan `request_delay_seconds` di konfigurasi.
