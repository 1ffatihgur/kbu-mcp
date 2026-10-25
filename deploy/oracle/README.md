# Oracle Cloud Always Free ile ücretsiz Remote MCP

Oracle'ın her zaman ücretsiz katmanı (Ampere A1 ARM: 2 OCPU, 12 GB RAM, 200 GB disk) KBÜ MCP'yi
7/24 çalıştırmaya fazlasıyla yeter. Sonuç: `https://<ip>.sslip.io/mcp` gibi, alan adı gerektirmeyen,
otomatik HTTPS'li bir adres.

## 1. Hesap

1. [oracle.com/cloud/free](https://www.oracle.com/cloud/free/) → **Start for free**.
2. E-posta doğrulaması, ardından bilgiler. **Home Region** sonradan değiştirilemez: yakın ve ARM kapasitesi
   bol bir bölge seçin (ör. *Germany Central (Frankfurt)*, *Netherlands Northwest (Amsterdam)*).
3. Kredi kartı doğrulaması (ücret alınmaz). Hesap **Free Tier** olarak kalır.

## 2. Sanal makine (VM)

**☰ Menü → Compute → Instances → Create instance**

| Alan | Değer |
|---|---|
| Name | `kbu-mcp` |
| Image | **Change image → Canonical Ubuntu 24.04** |
| Shape | **Change shape → Ampere → VM.Standard.A1.Flex**, **2 OCPU**, **12 GB** |
| Networking | Yeni VCN + **public subnet**, **Assign a public IPv4 address: açık** |
| Add SSH keys | **Upload public key files (.pub)** → kendi `.pub` anahtarınız |
| Boot volume | **Specify a custom boot volume size: 100 GB** |

**Create**. *"Out of capacity"* hatası alırsanız başka bir **Availability Domain** seçin ya da bir süre sonra
tekrar deneyin.

## 3. 80 ve 443 portlarını açın

Instance sayfası → **Primary VNIC → Subnet** → **Security Lists** → *Default Security List* →
**Add Ingress Rules**: Source CIDR `0.0.0.0/0`, IP Protocol **TCP**, Destination Port Range `80,443` → **Add**.

## 4. Kurulum

```bash
ssh ubuntu@<PUBLIC_IP>
git clone https://github.com/1ffatihgur/kbu-mcp.git && cd kbu-mcp/deploy/oracle
bash setup.sh            # kendi alan adınız varsa: DOMAIN=mcp.ornek.com bash setup.sh
```

İsteğe bağlı: elinizde bir indeks anlık görüntüsü (`kbu snapshot export`) varsa önce sunucuya kopyalayın,
ilk 1-2 saatlik tarama atlanır:

```bash
scp kbu.db.gz ubuntu@<PUBLIC_IP>:~/kbu.db.gz
```

Betik Docker'ı kurar, güvenlik duvarını açar, imajı GitHub'dan derler, Caddy ile Let's Encrypt sertifikasını
alır ve sonunda adresi yazar. İndeks kalıcı diskte durur; güncellemeler sunucunun içinde döner.

## Bakım

```bash
cd ~/kbu-mcp/deploy/oracle
sudo docker compose logs -f kbu            # loglar
curl https://<adres>/health                # durum
git pull && bash setup.sh                  # yeni sürüme geçiş
```
