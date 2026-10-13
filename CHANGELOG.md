# Değişiklikler

## 2026-10-13 — Repodan kurulabilir ilk sürüm

- ZIP içindeki kaynaklar repo köküne taşındı; özgün ZIP korundu.
- Git clone ile kurulum, kapasite gereksinimleri ve bakım komutları belgelendi.
- API bağlantı hataları güvenli biçimde ele alınıyor; keşif/rate-limit hataları
  yerel runner lifetime kontrolünü durdurmuyor.
- Runner'ın iş aldığı bilgisi journal'a yazılıyor; restart sonrası geçici API
  belirsizliği çalışan bir işin idle timeout ile silinmesine yol açmıyor.
- Controller ve preflight varsayılan kapasitesi iki runner olarak eşitlendi.
- Preflight repo include/exclude ayarlarını dikkate alıyor.
- Özelleştirilmiş RUNNER_IMAGE tag'i build sırasında da kullanılıyor.
- Operasyon label filtreleri ve entrypoint kullanan tanılama komutları düzeltildi.
- Docker smoke testi psql ile gerçek SQL sorgusu çalıştırıyor; PostgreSQL ilk
  açılışındaki geçici hazır sinyali TCP/SQL doğrulamasıyla eleniyor.
- Runner Dockerfile legacy Docker builder ile de build edilebiliyor.
- GitHub API, restart ve kesinti regresyonlarıyla toplam 34 test bulunuyor.
- Yerel doğrulama ve GitHub Actions aynı scripts/check.sh komutunu kullanıyor.
- CI Compose kontrolü secrets içermeyen örnek ayarlardan geçici .env oluşturuyor;
  temiz checkout kurulumu ayrıca doğrulandı.
