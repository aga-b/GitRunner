# Doğrulama kaydı — 13 Ekim 2026

Bu kayıt, ZIP açıldıktan ve kaynaklar iyileştirildikten sonra bu çalışma
ortamında yapılan kontrolleri gösterir. Önceki sunucu deneyimi
`docs/PROJECT-STATUS.md` içinde ayrıca saklanır.

| Kontrol | Sonuç |
|---|---|
| Python davranış ve regresyon testleri | 34/34 geçti |
| Python compileall ve bütün shell scriptleri için bash -n | Geçti |
| Compose config (.env.example ve yerel build ayarları) | Geçti |
| Temiz git clone üzerinde CI adımları | 34 test ve geçici .env ile Compose kontrolü geçti; geçici dosya silindi |
| Runner Docker image build | Geçti; Ubuntu 24.04, legacy Docker builder ile de doğrulandı |
| Controller Docker image build | Geçti |
| Docker-in-Docker başlangıcı | Gerçek disposable daemon ile geçti |
| Runner Docker Compose / Buildx | 2.40.3 / 0.37.2, geçti |
| Runner apt-get, sudo, tool-cache yazma erişimi | Geçti |
| Runner PostgreSQL client | psql 16.15, geçti |
| Runner localhost PostgreSQL bağlantısı | PostgreSQL 17 container üzerinde SELECT 1 geçti |
| Test container/network temizliği | Smoke finally bloğuyla tamamlandı |
| GitHub App erişimi ve JIT runner kaydı | Bu ortamda App anahtarı bulunmadığı için çalıştırılmadı |
| Gerçek GitHub Actions job'u / paralel shard'lar | App yapılandırılmış hedef CI hostunda ayrıca doğrulanmalı |

## Tekrarlama

```bash
./scripts/check.sh
./scripts/prepare.sh
./scripts/build.sh
docker compose run --rm --no-deps controller python smoke.py
```

App kimlikleri ve PEM eklendikten sonra `./scripts/preflight.sh` ile GitHub
API erişimi ve hedef host kapasitesi de denetlenir. Son kabul testi
`examples/runner-smoke.yml` ile dört shard'ın global kapasite iki olacak şekilde
çalışması ve ardından aktif runner'ların temizlenmesidir.

Bu smoke testi gerçek Docker ve SQL bağlantısını doğrular; JIT kaydı ve GitHub'ın
job eşleştirmesini taklit etmez. Playwright browser execution bu turda ayrıca
çalıştırılmadı; runner image'ında ilgili sistem kütüphaneleri kuruldu.

## Build sırasında doğrulanan image kaynakları

- Runner base: `ghcr.io/actions/actions-runner@sha256:4ffadc0002b2581327e06101fc8c06cd189232baf79fe561fac9caeb76f5e807`
- DinD: `docker.io/library/docker@sha256:0b6d18a4a222e71c88be6034926b4f6cde2fc33c2218643d03ccfddc65995470`

Bunlar bu testin kaynak digest'leridir. `scripts/build.sh` kurulum/güncelleme
anında resmi güncel image'ları çözer ve kurulumun `.env` dosyasına yazar.
