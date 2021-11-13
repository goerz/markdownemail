# markdownemail.py

This is a script that I use as part of a `mutt` `sendmail` script to optionally
add an html version for a markdown-formatted plain text email while it is sent
to SMTP.

The code is adapted from https://github.com/Roguelazer/muttdown.

To enable conversion, the plain text email should start with a line `!m`, `!md`, or `!markdown`.

Attachments can be referenced for inline images by name, e.g.

```
![attached.png](attached.png)
```

These get converted to proper [cid links](https://stackoverflow.com/questions/4312687/).

In principle, this works also for links, but only in very few email clients
(Web Gmail is the only one I've found.)

For previewing, it is best to paste the markdown text into a [Gist](https://gist.github.com).
