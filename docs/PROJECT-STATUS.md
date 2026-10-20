# Proje durumu

Son güncelleme: 13 Ekim 2026

## Önceki çalışmadan aktarılan kayıt

Aşağıdaki canlı sunucu sonuçları ZIP içindeki önceki çalışma notlarıdır. Bu repo
hazırlanırken yeniden ölçülen sonuçlar VALIDATION.md dosyasında ayrı tutulur.

## Tamamlanan çalışmalar

- Docker tabanlı `github-runner-autoscaler` kuruldu.
- İzin verilen kişisel repoların otomatik keşfi ve izlenmesi yapılandırıldı.
- `self-hosted,linux,x64,ci-linux` runner etiketleri kullanıldı.
- Global paralellik 20 (32 çekirdek / 64 GB host), bellek tabanlı kabul kontrolü ile.
- Controller'ın iki geçici `agah-ci-*` runner açtığı ve tamamlanınca
  `retired` lifecycle mesajlarıyla temizlediği doğrulandı.
- CI işlerinin self-hosted runner üzerinde tamamlanabildiği doğrulandı.
- Birden fazla CI işinin self-hosted kuyruğa geldiği görüldü.
- Workflow'larda `ci-linux` etiketlerini zorunlu tutan değişiklik hazırlandı.
- Runner dashboard compose denendi; token giriş ekranı nedeniyle dashboard
  kimlik/yapılandırma akışı henüz tamamlanmadı.

## Son test bulguları

| Bulgu | Etki | Repo içindeki çözüm |
|---|---|---|
| Playwright dependency adımında `sudo: apt-get: command not found` | Browser tabanlı test kurulumu duruyor | Runner build'i `apt-get` ve `sudo` varlığını build anında doğrular |
| PostgreSQL hazırlığında `psql: command not found` | Integration job exit 127 ile bitiyor | Runner image'a `postgresql-client` eklendi |
| Eski image ile başlatılan CI run'ları başarısız | Zorunlu kontroller geçmiyor | Image rebuild + yeni runner + yeni workflow run gerekli |

Bu iki hata mevcut çalışan container'a paket kurularak kalıcı biçimde
çözülmez. `scripts/build.sh` ile image yeniden üretilmeli; ephemeral runner'lar
bir sonraki işte yeni image'dan açılmalıdır.

## Sonraki uygulama adımları

1. Sunucuda bu repo güncellenir ve `./scripts/build.sh` çalıştırılır.
2. `./scripts/preflight.sh` ile host, image ve disposable PostgreSQL testi geçirilir.
3. Controller drain moduna alınır; aktif iş sıfırlandıktan sonra yeniden oluşturulur.
4. `examples/runner-smoke.yml` dört shard ve paralel eşzamanlı runner ile koşturulur.
5. Yeni commit/run ile CI tekrar test edilir; eski run'ı rerun etmek yerine yeni
   image kullanan runner oluştuğu controller logundan teyit edilir.
6. Repo tarafındaki zorunlu CI kontrollerinin sonuçları kaydedilir.
7. Dashboard token'ı secret/env üzerinden verilir; UI'da kalıcı token girişi
   yapılmaz. Dashboard salt-okunur operasyon ekranı olarak sınırlanır.

## Kabul kriterleri

- Aynı anda en fazla `MAX_RUNNERS` (20) runner aktiftir.
- Her runner yalnızca bir job tamamlar ve ardından silinir.
- Paralel PostgreSQL service container'ları port çakışması yaşamadan çalışır.
- Runner içinde `apt-get`, `sudo`, `psql`, Docker CLI ve Compose v2 bulunur.
- Controller restart aktif işi kesmez.
- Job sonrası ilgili container, network, volume ve GitHub kaydı kalmaz.
- Başarısız job logları secret sızdırmadan incelenebilir.

## Kapsam dışı / ertelenen

- Organizasyon seviyesinde runner pool'u.
- Kubernetes tabanlı Actions Runner Controller (ARC).
- Windows ve macOS runner'lar.
- Dörtten fazla paralel iş; mevcut host buna uygun değildir.
- Dashboard'dan yazma/yönetim işlemleri.
