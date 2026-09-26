# systemd units for Mnemosyne

USER units, same conventions as `knowledge-store/deploy/`: every command needs
`--user`, and `loginctl enable-linger $USER` is required for them to survive logout.

```bash
cd ~/Projects/Chiron/mnemosyne
cargo build --release                      # the unit runs the release binary
cp deploy/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now chiron-mnemosyne.service
systemctl --user status chiron-mnemosyne
journalctl --user -u chiron-mnemosyne -f
```

| Unit | Role |
|---|---|
| `chiron-mnemosyne-migrate.service` | Applies any missing migrations, then exits (oneshot) |
| `chiron-mnemosyne.service` | Runs the API on `127.0.0.1:8081` |

The Postgres cluster is managed by `chiron-ks-postgres.service` (in
`knowledge-store/deploy/`) — one server, two separate databases, `mnemosyne` and
`chiron_ks`. Install that unit first if you have not already.
