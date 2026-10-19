# Removing repeated login should persist device authorization, not remove server authentication

- **Problem**: Closing the native app discarded the service token, forcing repeated manual setup.
- **Dead end**: Removing authentication from the public service or embedding a shared token in an installable app would expose trading access. Plain localStorage is not a native credential store.
- **Effective path**: Keep backend bearer authentication, store the authorized session in iOS Keychain or Android Keystore encrypted preferences, restore it at startup, and keep first-time setup in collapsed settings. Display caches remain separately scoped by service, authorization and account.
- **Rule**: Fix session lifetime at the device boundary while preserving the server authorization boundary. Never log bridge payloads or bundle credentials.
- **Scope**: `mobile/src/deviceSession.ts`, native `DeviceSession` plugins, Capacitor logging configuration. Native compile/signing is verified; persistence across physical launches requires device runtime acceptance.
