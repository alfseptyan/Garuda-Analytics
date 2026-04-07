# Garuda Analytics MVP (Data Pipeline)

Proyek ini adalah MVP pipeline data untuk mengumpulkan data harga pasar pemain dari Transfermarkt dan berita terkait dari Google News RSS, lalu menyimpan hasilnya ke CSV lokal.

## Struktur
- `src/garuda_mvp.py`: script ETL utama
- `config/players.json`: daftar pemain dan sumber data
- `output/`: lokasi hasil CSV

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

Output akan tersimpan di `output/timnas_mvp_data.csv`.

## Catatan
- Scraping web dapat berubah sewaktu-waktu karena struktur HTML.
- Jika Transfermarkt memblokir, coba naikkan `request_delay_seconds` di konfigurasi.
