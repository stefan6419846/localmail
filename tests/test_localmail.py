# coding: utf-8

import os
import time
import threading
import imaplib
import smtplib
from io import BytesIO
from email.charset import Charset, BASE64, QP
from email.message import Message
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
try:
    from email.generator import BytesGenerator as Generator
except ImportError:
    from email.generator import Generator
try:
    import unittest2 as unittest
except ImportError:
    import unittest  # NOQA

import localmail

from .helpers import (
    SMTPClient,
    IMAPClient,
    clean_inbox,
)

thread = None

HOST = 'localhost'
SMTP_PORT = 2025
IMAP_PORT = 2143
HTTP_PORT = 8880

if 'LOCALMAIL' in os.environ:
    # use external server
    LOCALMAIL = os.getenv('LOCALMAIL')
    if ':' in LOCALMAIL:
        HOST, ports = LOCALMAIL.split(':')
        SMTP_PORT, IMAP_PORT, HTTP_PORT = ports.split(',')
else:
    # use random ports
    def report(smtp, imap, http):
        global SMTP_PORT, IMAP_PORT, HTTP_PORT
        SMTP_PORT = smtp
        IMAP_PORT = imap
        HTTP_PORT = http

    def setUpModule():
        global thread
        thread = threading.Thread(
            target=localmail.run, args=(0, 0, 0, None, report))
        thread.start()
        time.sleep(1)

    def tearDownModule():
        localmail.shutdown_thread(thread)


class BaseLocalmailTestcase(unittest.TestCase):

    def setUp(self):
        super(BaseLocalmailTestcase, self).setUp()
        self.addCleanup(clean_inbox, HOST, IMAP_PORT)


class AuthTestCase(BaseLocalmailTestcase):

    def test_smtp_any_auth_allowed(self):
        smtp = smtplib.SMTP(HOST, SMTP_PORT)
        smtp.login('a', 'b')
        smtp.sendmail('a@b.com', ['c@d.com'], 'Subject: test\n\ntest')
        smtp.quit()
        smtp = smtplib.SMTP(HOST, SMTP_PORT)
        smtp.login('c', 'd')
        smtp.sendmail('a@b.com', ['c@d.com'], 'Subject: test\n\ntest')
        smtp.quit()

    def test_smtp_anonymous_allowed(self):
        smtp = smtplib.SMTP(HOST, SMTP_PORT)
        smtp.sendmail('a@b.com', ['c@d.com'], 'Subject: test\n\ntest')
        smtp.quit()

    def test_imap_any_auth_allowed(self):
        imap = imaplib.IMAP4(HOST, IMAP_PORT)
        imap.login('any', 'thing')
        imap.select()
        self.assertEqual(imap.search('ALL'), ('OK', [None]))
        imap.close()
        imap.logout()

        imap = imaplib.IMAP4(HOST, IMAP_PORT)
        imap.login('other', 'something')
        imap.select()
        self.assertEqual(imap.search('ALL'), ('OK', [None]))
        imap.close()
        imap.logout()

    def test_imap_anonymous_not_allowed(self):
        imap = imaplib.IMAP4(HOST, IMAP_PORT)
        with self.assertRaises(imaplib.IMAP4.error):
            imap.select()
            self.assertEqual(imap.search('ALL'), ('OK', [None]))


class SequentialIdTestCase(BaseLocalmailTestcase):
    uid = False

    def setUp(self):
        super(SequentialIdTestCase, self).setUp()
        self.smtp = SMTPClient(HOST, SMTP_PORT)
        self.smtp.start()
        self.imap = IMAPClient(HOST, IMAP_PORT, uid=self.uid)
        self.imap.start()
        msgs = self.imap.search('ALL')
        self.assertEqual(msgs, [])
        self.addCleanup(self.smtp.stop)
        self.addCleanup(self.imap.stop)

    def _testmsg(self, n):
        msg = MIMEText("test %s" % n)
        msg['Subject'] = "test %s" % n
        msg['From'] = 'from%s@example.com' % n
        msg['To'] = 'to%s@example.com' % n
        return msg

    def assert_message(self, msg, n):
        expected = self._testmsg(n)
        self.assertEqual(msg['From'], expected['From'])
        self.assertEqual(msg['To'], expected['To'])
        self.assertEqual(msg['Subject'], expected['Subject'])
        self.assertEqual(msg.is_multipart(), expected.is_multipart())
        if msg.is_multipart():
            for part, expected_part in zip(msg.walk(), expected.walk()):
                self.assertEqual(part.get_content_maintype(),
                                 expected_part.get_content_maintype())
                if part.get_content_maintype() != 'multipart':
                    self.assertEqual(part.get_payload().strip(),
                                     expected_part.get_payload().strip())
        else:
            self.assertEqual(msg.get_payload().strip(),
                             expected.get_payload().strip())

    def test_simple_message(self):
        self.smtp.send(self._testmsg(1))
        msg = self.imap.fetch(1)
        self.assert_message(msg, 1)

    def test_multiple_messages(self):
        self.smtp.send(self._testmsg(1))
        self.smtp.send(self._testmsg(2))
        msg1 = self.imap.fetch(1)
        msg2 = self.imap.fetch(2)
        self.assert_message(msg1, 1)
        self.assert_message(msg2, 2)

    def test_delete_single_message(self):
        self.smtp.send(self._testmsg(1))
        self.imap.store(1, r'(\Deleted)')
        self.imap.client.expunge()
        self.assertEqual(self.imap.search('ALL'), [])

    def test_delete_with_multiple(self):
        self.smtp.send(self._testmsg(1))
        self.smtp.send(self._testmsg(2))
        self.imap.store(1, r'(\Deleted)')
        self.imap.client.expunge()
        self.assertEqual(self.imap.search('ALL'), [self.imap.msgid(1)])

    def test_search_deleted(self):
        self.smtp.send(self._testmsg(1))
        self.smtp.send(self._testmsg(2))
        self.imap.store(1, r'(\Deleted)')
        self.assertEqual(
            self.imap.search('(DELETED)'),
            [self.imap.msgid(1)]
        )
        self.assertEqual(
            self.imap.search('(NOT DELETED)'),
            [self.imap.msgid(2)]
        )


class UidTestCase(SequentialIdTestCase):
    uid = True


class MultipartTestCase(SequentialIdTestCase):

    def _testmsg(self, n):
        msg = MIMEMultipart('alternative')
        msg['Subject'] = 'test %s' % n
        msg['From'] = 'from%s@example.com' % n
        msg['To'] = 'to%s@example.com' % n
        html = MIMEText('<b>test %s</b>' % n, 'html')
        text = MIMEText('test %s' % n, 'plain')
        msg.attach(html)
        msg.attach(text)
        return msg


class EncodingTestCase(BaseLocalmailTestcase):

    # These characters are one byte in latin-1 but two in utf-8
    difficult_chars = u"£ë"
    # These characters are two bytes in either encoding
    difficult_chars += u"筷子"
    # Unicode snowman for good measure
    difficult_chars += u"☃"
    # These characters might trip up Base64 encoding...
    difficult_chars += u"=+/"
    # ... and these characters might trip up quoted printable
    difficult_chars += u"=3D"  # QP encoded

    difficult_chars_latin1_compatible = difficult_chars\
        .encode('latin-1', 'ignore')\
        .decode('latin-1')

    uid = False

    def setUp(self):
        super(EncodingTestCase, self).setUp()
        self.smtp = SMTPClient(HOST, SMTP_PORT)
        self.smtp.start()
        self.imap = IMAPClient(HOST, IMAP_PORT, uid=self.uid)
        self.imap.start()
        msgs = self.imap.search('ALL')
        self.assertEqual(msgs, [])
        self.addCleanup(self.smtp.stop)
        self.addCleanup(self.imap.stop)

    def _encode_message(self, msg):
        with BytesIO() as fp:
            generator = Generator(fp)
            generator.flatten(msg)
            return fp.getvalue()

    def _make_message(self, text, charset, cte):
        msg = Message()
        ctes = {'8bit': None, 'base64': BASE64, 'quoted-printable': QP}
        cs = Charset(charset)
        cs.body_encoding = ctes[cte]
        msg.set_payload(text, charset=cs)

        # Should always be encoded correctly.
        msg['Subject'] = self.difficult_chars
        msg['From'] = 'from@example.com'
        msg['To'] = 'to@example.com'
        self.assertEqual(msg['Content-Transfer-Encoding'], cte)
        return msg

    def _fetch_and_delete_sole_message(self):
        message_number = None
        for _ in range(5):
            try:
                message_number, = self.imap.search('ALL')
                break
            except ValueError:
                time.sleep(0.5)
        else:
            raise AssertionError("Single Message not found")
        msg = self.imap.fetch(message_number)
        self.imap.store(message_number, r'(\Deleted)')
        self.imap.client.expunge()
        return msg

    def _do_test(self, payload, charset, cte):
        # Arrange
        msg = self._make_message(payload, charset, cte)
        encoded = self._encode_message(msg)

        # Act
        self.smtp.client.sendmail(msg['From'], msg['To'], encoded)
        received = self._fetch_and_delete_sole_message()

        # Assert
        payload_bytes = received.get_payload(decode=True)
        payload_text = payload_bytes.decode(received.get_content_charset())
        self.assertEqual(received['Content-Transfer-Encoding'], cte)
        self.assertEqual(received.get_content_charset(), charset.lower())
        (subject_bytes, subject_encoding), = decode_header(received['Subject'])
        self.assertEqual(
            subject_bytes.decode(subject_encoding),
            self.difficult_chars)
        self.assertEqual(payload_text.strip(), payload)

    def test_roundtrip_latin_1_mail(self):
        """
        Mail with only latin-1 chars can be sent in latin-1

        (8-bit MIME)
        """
        self._do_test(self.difficult_chars_latin1_compatible,
                      'iso-8859-1', '8bit')

    def test_roundtrip_utf8_mail(self):
        """
        Mail can be sent in utf-8 without encoding

        (8-bit MIME)
        """
        self._do_test(self.difficult_chars, 'utf-8', '8bit')

    def test_roundtrip_utf8_qp_mail(self):
        """
        Mail can be sent in utf-8 in quoted printable format
        """
        self._do_test(self.difficult_chars, 'utf-8', 'quoted-printable')

    def test_roundtrip_utf8_base64_mail(self):
        """
        Mail can be sent in utf-8 in quoted printable format
        """
        self._do_test(self.difficult_chars, 'utf-8', 'base64')
