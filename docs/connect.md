# Connect to CrossWatch

How to connect the add-on to your CrossWatch, check the connection and undo it.

## Pairing

This is the normal way to connect. In the add-on settings, CrossWatch category, select "Pair
with CrossWatch".

<img src="https://github.com/user-attachments/assets/a3cfda91-bc75-4deb-b217-530ee5abb142" alt="Add-on settings: Pair with CrossWatch, Status, Unpair" width="600">

1. In CrossWatch, open the Kodi instance you want this device to use and ask it for a pairing
   code.
2. Back in the add-on, type the CrossWatch address: `192.168.1.10:8787` is enough, `http://` is
   assumed if you leave it off, and a trailing slash or a full webhook URL both work too.

   ![Address prompt](https://github.com/user-attachments/assets/c6b62c2d-ac0a-44a2-be2d-c9b3774cfbfd)

3. Type the code it gave you: 6 characters, capitals and digits, valid for 10 minutes and good
   for one use. Case and spaces do not matter, so `ab 23 cd` and `AB23CD` are the same.

   ![Pairing code prompt](https://github.com/user-attachments/assets/2f8d8be9-c55c-4eb7-b70d-d502ecbee397)

On success you get a notification naming the CrossWatch instance, and the Status line
(read-only, just under the Pair button) reads "Paired with `<instance>` at `<address>`".

![Paired notification](https://github.com/user-attachments/assets/31dc03c9-43b0-4062-8323-4b4912b91257)

If it does not succeed, nothing is changed: the previous connection, if any, stays in place.
What you see depends on what went wrong:

- A wrong or expired code: "Code wrong or expired. Get a new code in CrossWatch."
- Too many tries: "Too many tries. Wait a minute and try again."
- CrossWatch cannot be reached at that address: "Can't reach CrossWatch at `<address>`."
- Some other failure on CrossWatch's side: "Pairing failed: CrossWatch answered HTTP `<code>`."
- The Kodi add-on is switched off in CrossWatch: "The Kodi add-on is switched off in
  CrossWatch. Turn it on there, then pair again."
- An HTTPS certificate the add-on does not trust: "The certificate of `<address>` was not
  accepted. HTTPS needs a certificate from a public certificate authority; a self-signed one
  does not work."
- An address that cannot be used, such as one starting with `ftp://`: "Not a CrossWatch
  address: `<address>`"

![Wrong code message](https://github.com/user-attachments/assets/12e7591b-ac25-4e7e-915d-02ef55248283)

Each of these appears in a dialog on screen.

Pressing Pair or Unpair closes the settings screen first, saving anything else you changed
there. The add-on switches to the new connection as soon as you finish, no Kodi restart
needed, and sends a heartbeat right away, so CrossWatch sees it within seconds.

## Link (shortcut)

Link only works when CrossWatch already controls this Kodi through Kodi's web interface
(JSON-RPC), which is off by default. Most households will not have this on and should pair
instead.

When it applies, CrossWatch starts it: the TV shows "Link this Kodi to CrossWatch at
`<address>`?" with No already selected.

![Link question on the TV](https://github.com/user-attachments/assets/46d8a992-096d-4855-b52e-a59df31bce4f)

Choosing Yes connects, using a one-time code CrossWatch sent along, so a Link that fails
shows the same messages as [pairing](#pairing). Choosing No, or not answering within 60 seconds,
changes nothing.

## The Status line

This line is read-only; it only ever reports what the add-on last did. It reads one of:

- "Paired with `<instance>` at `<address>`", after pairing or a Link.
- "Not paired", when nothing is connected.

## Unpair

The "Unpair" button appears only while the add-on is connected. It asks for confirmation, then
stops reporting. Any completed watches already kept on disk for delivery during an outage are
not deleted: they are still sent if you pair again with the same CrossWatch instance. Moving to
a different CrossWatch does not need Unpair first, just pair again.

## Manual setup (advanced)

If you would rather not pair, "Webhook URL" and "Webhook token" are available at the Advanced
settings level (use the settings level button on Kodi's settings screen to change the level).

![Settings at the Advanced level](https://github.com/user-attachments/assets/e9d8afce-11e0-44ae-ba1c-9ded5f327c1b)

CrossWatch's instance page also offers a manual option: a full URL with `?token=` in it. You
can paste that whole URL into "Webhook URL"; the add-on takes the token out of it and moves it
into "Webhook token" for you. Whichever way the token gets there, it is only ever sent in a
request header, never in the URL.
