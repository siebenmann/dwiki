#
# The specific model classes and functions for comments.
# This is separate from model.py because it is getting big.

import re

import utils
import pages

import derrors, storage

# ---
# Original (legacy) comment format, which has only DWiki login and
# comment IP address.

# Comments are stored in a packed form.
commentbody_re = re.compile("USER ([^\n]+)\nIP ([^\n]+)\n(.*)$",
			    re.DOTALL)
class Comment:
	def __init__(self, context = None, data = None):
		if context:
			self.user = context.login
			self.ip = context["remote-ip"]
		else:
			self.user = None
			self.ip = None
		if not data:
			self.data = ''
		else:
			self.data = data
		self.time = None
		self.name = None
		self.username = ''
		self.userurl = ''
		self.anon = None
	def __str__(self):
		if not self.data:
			return ''
		return 'USER %s\nIP %s\n%s' % (self.user, self.ip, self.data)
	def fromstore(self, fileobj, name):
		blob = fileobj.contents()
		if not blob:
			return False
		mo = commentbody_re.match(blob)
		if not mo:
			return False
		self.user = mo.group(1)
		self.ip = mo.group(2)
		self.data = mo.group(3)
		self.time = fileobj.timestamp()
		self.name = name
		return True

	def is_anon(self, context):
		return self.user == context.default_user()

# ---
# New format comments now include an explicit version field as the first
# field.
commentver_re = re.compile("^VER (\d+)\n")

# V1 new comment format. This adds a user-supplied name and URL so that
# DWiki acts more like traditional blog comments and while I'm at it,
# a marker of whether the DWiki login was the default/anonymous/guest
# user at the time of comment submission (... in case you remove or
# change it later or something).
#
commentv1_re = re.compile("^VER 1\nUSER ([^\n]+)\nANON (Yes|No)\nNAME ([^\n]*)\nURL ([^\n]*)\nIP ([^\n]+)\n(.*)$",
			  re.DOTALL)
class CommentV1:
	def __init__(self):
		self.user = None
		self.anon = None
		self.ip = None
		self.data = ''
		self.username = ''
		self.userurl = ''
		self.time = None
		self.name = None

	def __str__(self):
		if not self.data:
			return ''
		return "VER 1\nUSER %s\nANON %s\nNAME %s\nURL %s\nIP %s\n%s" % \
		       (self.user, self.anon,
			self.username, self.userurl, self.ip,
			self.data)
	# Called only if the blob is non-null and asserts to be a V1 format.
	def fromstore(self, fileobj, name):
		blob = fileobj.contents()
		if not blob:
			raise derrors.IntErr("CommentV1 fromstore blob is empty")
		mo = commentv1_re.match(blob)
		if not mo:
			return False
		self.user = mo.group(1)
		self.anon = mo.group(2)
		self.username = mo.group(3).strip()
		self.userurl = mo.group(4).strip()
		self.ip = mo.group(5)
		self.data = mo.group(6)
		self.time = fileobj.timestamp()
		self.name = name
		return True

	def fromform(self, context, data, username, userurl):
		self.user = context.login
		self.ip = context["remote-ip"]
		self.data = data
		self.username = username
		self.userurl = userurl
		if context.is_login_default():
			self.anon = "Yes"
		else:
			self.anon = "No"

	def is_anon(self, _):
		return self.anon == "Yes"

# ----
# This loads comments in any known format from the disk, working out
# which format the comment is in itself. Returns the comment or None
# and may raise derrors.IntErr in some situations.

def loadcomment(fileobj, name):
	blob = fileobj.contents()
	if not blob:
		return None
	mo = commentver_re.match(blob)
	# might be a version zero comment.
	if not mo:
		mo = commentbody_re.match(blob)
		if mo:
			c = Comment()
		else:
			return None
	elif mo.group(1) == "1":
		c = CommentV1()
	else:
		raise derrors.IntErr("Uknown comment format version: '%s' in %s" %
				     (mo.group(1), name))

	# Load:
	if c.fromstore(fileobj, name):
		return c
	else:
		return None

# TODO: should we have a createcomment() function? Probably not, since the
# arguments are likely to keep evolving.
