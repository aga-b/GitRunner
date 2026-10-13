# Güvenlik politikası

Bu yazılım Docker host socket'ini yönetir ve `privileged` DinD container'ları
oluşturur. Yalnızca ayrılmış bir CI VM'inde ve güvenilen workflow koduyla
kullanılmalıdır. Üretim hostuna doğrudan kurulması önerilmez.

Güvenlik açığı bildirimine GitHub issue içinde secret, private key, token,
workflow logu veya kurum içi adres eklemeyin. Private bildirim kanalı yoksa
önce depo sahibine yalnızca etkilenmiş bileşen ve iletişim talebiyle ulaşın.

## Secret kuralları

- GitHub App private key sadece `secrets/github-app.pem` içinde tutulur.
- `.env`, `secrets/` ve `state/` Git tarafından yok sayılır.
- Token ve JIT config loglara yazılmaz. JIT config Docker'a environment ile
  aktarılır; resmi runner kendi process argümanında bunu kullanır. Host Docker
  yöneticileri container environment'ını inceleyebildiği için host erişimi de
  güven sınırının içindedir.
- Bir secret yanlışlıkla commit edilirse dosyayı silmek yeterli değildir;
  secret derhal revoke/rotate edilmelidir.
