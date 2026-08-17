# Bridge secrets

Everything in this folder except this file is gitignored.

Create a file here called `env` containing your Supabase credentials:

```
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-service-role-key
```

Then restrict it:

```sh
chmod 600 secrets/env
```

⚠️ This is the **service_role** key, not the anon key. It bypasses all database
security, so treat it like a password and never commit it.

Environment variables of the same names override this file, which is how you'd
inject them from systemd instead if you prefer.

See [BUILD.md](../../BUILD.md) step 4.5.
