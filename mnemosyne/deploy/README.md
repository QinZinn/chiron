# Unit systemd cho Mnemosyne

USER unit, cùng quy ước với `knowledge-store/deploy/`: mọi lệnh phải có
`--user`, và cần `loginctl enable-linger $USER` để chúng sống qua logout.

```bash
cd ~/Projects/Chiron/mnemosyne
cargo build --release                      # unit chạy binary release
cp deploy/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now chiron-mnemosyne.service
systemctl --user status chiron-mnemosyne
journalctl --user -u chiron-mnemosyne -f
```

| Unit | Việc |
|---|---|
| `chiron-mnemosyne-migrate.service` | Áp dụng migration còn thiếu rồi thoát (oneshot) |
| `chiron-mnemosyne.service` | Chạy API ở `127.0.0.1:8081` |

Cụm Postgres do `chiron-ks-postgres.service` (trong `knowledge-store/deploy/`)
quản lý — một server, hai database riêng `mnemosyne` và `chiron_ks`. Cài unit đó
trước, nếu chưa có.
