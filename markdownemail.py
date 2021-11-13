#!/usr/bin/env python
# Adapted from https://github.com/Roguelazer/muttdown

import argparse
import sys
import smtplib
import re
import os.path
import email
import quopri
import email.iterators
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import click
import pycmarkgfm
from bs4 import BeautifulSoup


def convert_md(text):
    options = (
        pycmarkgfm.options.unsafe
        | pycmarkgfm.options.hardbreaks
        | pycmarkgfm.options.smart
    )
    return pycmarkgfm.gfm_to_html(text, options=options)


def rewrite_attachment_urls(html, attachment_names):
    """Rewrite img/links to local attachments to "cid" urls.

    This works great for images, but not so well for links. Links seem to work
    in Gmail, but not much elsewhere.
    """
    rx = re.compile(r'href="(?P<name>[^/]+?)"')
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a"):
        url = a["href"]
        if not url.startswith("#") and ":" not in url:
            if url not in attachment_names:
                raise ValueError("Link %s does not point to attachment" % url)
            a["href"] = "cid:" + content_id(url)
    for img in soup.find_all("img"):
        url = img["src"]
        if ":" not in url:
            if url not in attachment_names:
                raise ValueError("Img %s does not point to attachment" % url)
            img["src"] = "cid:" + content_id(url)
    return str(soup)



MARKER = re.compile(r'^\s*!(md?|markdown)\s*\n', flags=re.M)


def convert_one(part, attachment_names=None):
    if attachment_names is None:
        attachment_names = []
    text = part.get_payload()
    if not MARKER.match(text):
        return None
    text = MARKER.sub('', text, count=1)
    if '\n-- \n' in text:
        pre_signature, signature = text.split('\n-- \n')
        html = convert_md(pre_signature)
        html += '\n<pre class="signature" style="font-size: small">-- \n'
        html += signature
        html += '</pre>'
    else:
        html = convert_md(text)
    css_file = Path(__file__).parent / "style.css"
    if css_file.is_file():
        css = Path(css_file).read_text()
        html = '<style>' + css + '</style>' + html
        import pynliner

        html = pynliner.fromString(html)
    html = rewrite_attachment_urls(html, attachment_names=attachment_names)
    message = MIMEText(html, 'html', _charset="UTF-8")
    return message


def _move_headers(source, dest):
    for k, v in source.items():
        # mutt sometimes sticks in a fake bcc header
        if k.lower() == 'bcc':
            del source[k]
        elif not (k.startswith('Content-') or k.startswith('MIME')):
            dest.add_header(k, v)
            del source[k]


def content_id(filename):
    sanitized_filename = (
        quopri.encodestring(filename.encode("utf-8"))
        .decode("ascii")
        .replace(" ", "_")
    )
    return f"{sanitized_filename}@attached"


def get_attachment_names(message):
    filenames = []
    for part in message.walk():
        # this part comes from the snipped I don't understand yet...
        if part.get_content_maintype() == 'multipart':
            continue
        if part.get('Content-Disposition') is None:
            continue
        filename = part.get_filename()
        if filename is not None:
            filenames.append(filename)
    return filenames


def convert_tree(
    message, indent=0, wrap_alternative=True, attachment_names=None
):
    """Recursively convert a potentially-multipart tree.

    Returns a tuple of (the converted tree, whether any markdown was found)
    """
    if attachment_names is None:
        attachment_names = get_attachment_names(message)
    ct = message.get_content_type()
    cs = message.get_content_subtype()
    if not message.is_multipart():
        # we're on a leaf
        converted = None
        disposition = message.get('Content-Disposition', 'inline')
        if disposition == 'inline' and ct in ('text/plain', 'text/markdown'):
            converted = convert_one(message, attachment_names=attachment_names)
            # Modify the original message to strip out the MARKER
            text = message.get_payload()
            message['Content-Transfer-Encoding'] = '8bit'
            text = MARKER.sub('', text, count=1)
            message.set_payload(text, "UTF-8")
            # See https://stackoverflow.com/a/61215637
            del message['Content-Transfer-Encoding']
            email.encoders.encode_7or8bit(message)
        elif disposition.startswith("attachment"):
            filename = message.get_filename()
            sanitized_filename = (
                quopri.encodestring(filename.encode("utf-8"))
                .decode("ascii")
                .replace(" ", "_")
            )
            message.add_header('Content-ID', "<" + content_id(filename) + ">")
        if converted is not None:
            if wrap_alternative:
                new_tree = MIMEMultipart('alternative')
                _move_headers(message, new_tree)
                new_tree.attach(message)
                new_tree.attach(converted)
                return new_tree, True
            else:
                return converted, True
        return message, False
    else:
        if ct == 'multipart/signed':
            # if this is a multipart/signed message, then let's just
            # recurse into the non-signature part
            new_root = MIMEMultipart('alternative')
            if message.preamble:
                new_root.preamble = message.preamble
            _move_headers(message, new_root)
            converted = None
            for part in message.get_payload():
                if part.get_content_type() != 'application/pgp-signature':
                    converted, did_conversion = convert_tree(
                        part,
                        indent=indent + 1,
                        wrap_alternative=False,
                        attachment_names=attachment_names,
                    )
                    if did_conversion:
                        new_root.attach(converted)
            new_root.attach(message)
            return new_root, did_conversion
        else:
            did_conversion = False
            new_root = MIMEMultipart(cs, message.get_charset())
            if message.preamble:
                new_root.preamble = message.preamble
            _move_headers(message, new_root)
            for part in message.get_payload():
                part, did_this_conversion = convert_tree(
                    part,
                    indent=indent + 1,
                    attachment_names=attachment_names,
                )
                did_conversion |= did_this_conversion
                new_root.attach(part)
            return new_root, did_conversion


def process_message(message):
    converted, did_any_markdown = convert_tree(message)
    if 'Bcc' in converted:  # TODO
        assert False
        del converted['Bcc']
    return converted


@click.command()
@click.help_option('-h', '--help')
@click.argument('input', type=click.File('rb'))
def main(input):
    """Filter eml file, extending markdown plain text with html."""
    message = email.message_from_bytes(input.read())
    message = process_message(message)
    mail = message.as_bytes()
    click.get_binary_stream('stdout').write(mail)


if __name__ == "__main__":
    main()
