# Connect to CrossWatch

The add-on needs to know where your CrossWatch is and needs a token that CrossWatch accepts.
Pairing sets up both in one go. You only do this once per Kodi device (and per Kodi profile;
see the note under [Kodi profile](who-watched.md#kodi-profile)).

## Pairing

Before you start, have CrossWatch open in a browser and know its address, for example
`192.168.1.10:8787`.

1. In CrossWatch, open the Kodi instance this device should use and ask it for a pairing code.
   The [CrossWatch wiki](https://wiki.crosswatch.app/crosswatch/settings/connections/media-clients/kodi/kodi-add-on)
   shows where.
2. In Kodi, open the add-on settings (Add-ons > My add-ons > Services > CrossWatch for Kodi > Configure)
   and, in the CrossWatch category, select "Pair with CrossWatch".

    <img src="https://github.com/user-attachments/assets/a3cfda91-bc75-4deb-b217-530ee5abb142" alt="Add-on settings: Pair with CrossWatch, Status, Unpair" width="600">

3. Type the CrossWatch address. `http://` is assumed if you leave it out, and a trailing slash or
   the full webhook URL is fine too.

    ![Address prompt](https://github.com/user-attachments/assets/c6b62c2d-ac0a-44a2-be2d-c9b3774cfbfd)

4. Type the pairing code: six letters and digits, valid for 10 minutes and for one use. Case
   and spaces do not matter, so `ab 23 cd` works for `AB23CD`.

    ![Pairing code prompt](https://github.com/user-attachments/assets/2f8d8be9-c55c-4eb7-b70d-d502ecbee397)

A notification names the CrossWatch instance, and the Status line under the Pair button reads
"Paired with `<instance>` at `<address>`". The add-on starts reporting straight away; there is
no need to restart Kodi.

![Paired notification](https://github.com/user-attachments/assets/31dc03c9-43b0-4062-8323-4b4912b91257)

Selecting Pair closes the settings screen and saves any other changes you made there. Leaving
the address or code empty cancels.

### If pairing fails

Nothing changes: an earlier connection stays as it was. The message says what went wrong:

| Message | What to do |
|---|---|
| Code wrong or expired. Get a new code in CrossWatch. | Ask CrossWatch for a new code. If it offers none, check that the Kodi add-on is switched on for this instance in CrossWatch. |
| Too many tries. Wait a few minutes and try again. | Wait, then pair with a new code. |
| Can't reach CrossWatch at `<address>`. | Check the address and port, and that Kodi can reach that machine. |
| Pairing failed: CrossWatch answered HTTP `<code>`. | Something went wrong on CrossWatch's side; check CrossWatch's log. |
| The certificate of `<address>` was not accepted. | An `https://` address needs a certificate from a public authority such as Let's Encrypt. Self-signed certificates do not work; use `http://` on your home network instead. |
| Not a CrossWatch address: `<address>` | Use an `http://` or `https://` address, with a port from 1 to 65535 if you give one. A URL containing `?token=` belongs in [Manual setup](#manual-setup-advanced). |

![Wrong code message](https://github.com/user-attachments/assets/12e7591b-ac25-4e7e-915d-02ef55248283)

## The Status line

- "Paired with `<instance>` at `<address>`": the add-on is connected, and CrossWatch answered
  at least once.
- "Not paired": no address or token is set, so nothing is reported.

The line records the last successful answer; it does not test the connection. It keeps saying
"Paired with ..." when CrossWatch is down or has since stopped accepting the token. If playback
does not show up in CrossWatch, see
[Nothing arrives in CrossWatch](troubleshooting.md#nothing-arrives-in-crosswatch).

## Link (shortcut)

If CrossWatch already controls this Kodi over JSON-RPC (Kodi's web server and remote control,
off by default in Kodi), CrossWatch can link the add-on without a code. You start it in
CrossWatch; the TV then asks "Link this Kodi to CrossWatch at `<address>`?".

![Link question on the TV](https://github.com/user-attachments/assets/46d8a992-096d-4855-b52e-a59df31bce4f)

Yes connects, exactly as pairing would, and shows the same messages if it fails. No, or no
answer within 60 seconds, changes nothing. Most households do not have JSON-RPC on and should
simply pair.

## Moving to another CrossWatch

Pair again with the new one; there is no need to unpair first. Completed watches still waiting
to be delivered to the old CrossWatch are then discarded, because they belong to the old token
(see [When CrossWatch cannot be reached](what-is-sent.md#when-crosswatch-cannot-be-reached)).
A new address for the same CrossWatch keeps them.

## Unpair

"Unpair" stops all reporting from this Kodi: after you confirm, it clears the address and
token. Completed watches still waiting for delivery are kept, and are sent if you later pair
again with the same CrossWatch and it hands out the same token.

## Manual setup (advanced)

You can set the connection by hand instead of pairing. Switch the settings screen to the
Advanced level (with the settings level button on Kodi's settings screen) to show
"Webhook URL" and "Webhook token".

![Settings at the Advanced level](https://github.com/user-attachments/assets/e9d8afce-11e0-44ae-ba1c-9ded5f327c1b)

- **Webhook URL**: the full endpoint, such as `http://192.168.1.10:8787/webhook/kodiwatcher`.
  It is used exactly as typed.
- **Webhook token**: the token from CrossWatch.

CrossWatch's instance page also offers the URL with `?token=` on the end. You can paste that
whole URL into "Webhook URL": when you press OK, the add-on moves the token into "Webhook
token". The token is only ever sent in a request header, never in the URL, so it does not end
up in proxy logs.

Pairing fills in these same two fields, so editing them changes a paired connection too.
