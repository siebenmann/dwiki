#
# Handle RCS files in various ways.
#
# Some parsing stuff taken from http://www.neuron.yale.edu/cgi-bin/viewcvs.cgi/viewcvs/lib/rcsparse.py?rev=HEAD&content-type=text/vnd.viewcvs-markup
# seeing as viewcvs already did this work.

import re
import os.path

class RCSErr(Exception):
	pass
class RCSParseErr(Exception):
	pass

CHUNK_SIZE = 16*1024
tokendre = re.compile(r"[\s;]")
class TokenStream:
	def __init__(self, fp):
		self.fp = fp
		self.pushback = []
		self.buf = ''
		self.refill()

	def refill(self, errEof = True):
		self.buf = self.fp.read(CHUNK_SIZE)
		if not self.buf and errEof:
			raise RCSParseErr, "unexpected EOF"

	def get(self, strallowed = True):
		if self.pushback:
			return self.pushback.pop(0)

		# Skip whitespace.
		while 1:
			self.buf = self.buf.lstrip()
			if not self.buf:
				self.refill(False)
				if not self.buf:
					return None
			else:
				break

		# At this point, self.buf[0] is some non-whitespace
		# character.
		if self.buf[0] == ';':
			self.buf = self.buf[1:]
			return ';'
		elif self.buf[0] != '@':
			# A token may span more than one buffer. (You
			# laugh, but ...)
			token = []
			while 1:
				mo = tokendre.search(self.buf)
				if mo:
					break
				token.append(self.buf)
				# Valid RCS files must end with a newline,
				# so if refill fails we have an error.
				self.refill()
			token.append(self.buf[:mo.start(0)])
			self.buf = self.buf[mo.start(0):]
			return ''.join(token)

		# Now we have an RCS string.
		# This may not be allowed; error if so.
		if not strallowed:
			raise RCSParseErr, "token is a string when no string is allowed"
		
		# Time to do the string dequote dance.
		chunks = []
		pos = 1
		while 1:
			if not self.buf:
				self.refill()
				pos = 0
			p = self.buf.find("@", pos)
			if p == -1:
				chunks.append(self.buf[pos:])
				self.buf = ''
			else:
				chunks.append(self.buf[pos:p])
				self.buf = self.buf[p+1:]
				pos = 0
				# Torture test case: we could have an
				# interior quoting '@@' split over two
				# buffers.
				if not self.buf:
					self.refill()
				# self.buf[0] is either the second '@' or
				# something else, or we died on EOF above.
				if self.buf[0] == '@':
					chunks.append('@')
					pos = 1
				else:
					break
		return ''.join(chunks)

	def unget(self, token):
		self.pushback.append(token)

class RCSParser:
	def __init__(self, fp):
		self.admin = {}
		self.deltas = {}
		self.ts = TokenStream(fp)
		self.desc = None

	# -- parsing support for internal usage
	def get(self):
		return self.ts.get()
	def gettok(self):
		tok = self.ts.get()
		if tok == None:
			raise RCSParseErr, "unexpected EOF"
		return tok
	def unget(self, tok):
		self.ts.unget(tok)
	def getstart(self):
		tok = self.ts.get(False)
		if tok == ';':
			raise RCSParseErr, "emtpy line"
		return tok
	def match(self, what):
		tok = self.getstart()
		if tok != what:
			raise RCSParseErr, "error parsing RCS file: expected token %s but saw '%s'" % (what, tok)
		return tok

	def setadmin(self, name, val):
		self.admin[name] = val
	def adddelta(self, delta):
		self.deltas[delta['rev']] = delta
	def addtext(self, rev, log, text):
		self.deltas[rev]['log'] = log
		self.deltas[rev]['text'] = text

#
# ----
# Utility crud for parsing the innards of RCS files.
def restline(rfo):
	ln = []
	while 1:
		t = rfo.gettok()
		if t == ';':
			break
		ln.append(t)
	return ln

# I decline to have one little routine to parse every header line, thanks.
# Fortunately none of the headers repeat. We don't care about any of the
# new phrase headers.
admheaders = ( "head", "branch", "access", "symbols", "locks", "strict",
	      "comment", "expand", )
deltare = re.compile("^\d+(\.\d+)*$")
def parseadmin(rfo):
	# Read things while we know about them.
	while 1:
		tok = rfo.getstart()
		if tok == None:
			return
		# Not a known token? Bye!
		if tok not in admheaders:
			break
		ln = restline(rfo)
		if tok == "strict":
			rfo.setadmin(tok, True)
		elif tok in ('head', 'branch'):
			rfo.setadmin(tok, ln[0])
		elif ln:
			rfo.setadmin(tok, ln)
	# Not a known header: it could be either the 'newphrase' stuff,
	# a delta start, or a desc start. If it is newphrases, we need
	# to skip them all.
	while 1:
		mo = deltare.match(tok)
		if mo or tok == "desc":
			break
		# swallow the rest of the line.
		restline(rfo)
		tok = rfo.getstart()
		if tok == None:
			return
	# Bingo! We're live.
	rfo.unget(tok)
	return

def matchline(rfo, what):
	rfo.match(what)
	ln = restline(rfo)
	if not ln:
		return None
	else:
		return ''.join(ln)
def deltaline(rfo, what, delta):
	delta[what] = matchline(rfo, what)

# We enter parsedeltas in a state where the only possible next tokens
# are either a delta number, 'delta', or EOF.
def parsedeltas(rfo):
	while 1:
		rev = rfo.getstart()
		if rev == "desc" or rev == None:
			rfo.unget(rev)
			return
		delta = {}
		delta['rev'] = rev
		deltaline(rfo, "date", delta)
		deltaline(rfo, "author", delta)
		deltaline(rfo, "state", delta)
		deltaline(rfo, "branches", delta)
		deltaline(rfo, "next", delta)
		rfo.adddelta(delta)
		# Now we skip over any extra phrases.
		while 1:
			tok = rfo.getstart()
			if not tok:
				return
			mo = deltare.match(tok)
			if mo or tok == "desc":
				break
			restline(rfo)
		rfo.unget(tok)
		if tok == "desc":
			return
	# we should never get here.

# The RCS file description is not actually ';' terminated. Thanks!
# We appreciate this degree of consistency!
def parsedesc(rfo):
	rfo.match("desc")
	tok = rfo.gettok()
	rfo.desc = tok

def parsedeltatexts(rfo):
	while 1:
		rev = rfo.getstart()
		if not rev:
			return
		rfo.match("log")
		log = rfo.get()
		# Find and skip any new phrases.
		while 1:
			tok = rfo.getstart()
			if tok == "text":
				break
			if tok == None:
				raise RCSParseErr, "unexpected EOF"
			restline(rfo)
		# Now we get the actual text, which is the next token.
		# Period.
		text = rfo.get()
		if not text or not log:
			raise RCSParseErr, "unexpected EOF"
		rfo.addtext(rev, log, text)
		# and repeat until EOF.
	# never reached.

def runparse(fp):
	rfo = RCSParser(fp)
	parseadmin(rfo)
	parsedeltas(rfo)
	parsedesc(rfo)
	parsedeltatexts(rfo)
	return rfo

##
# The actual class that people are supposed to use.
# We store the name of the RCS file because for some operations we
# need to actually call RCS commands directly (specifically, retrieving
# non-head versions of files).
class RCSFile:
	def __init__(self, rfo, fname):
		self.admin = rfo.admin
		self.deltas = rfo.deltas
		self.rname = fname

	# ----
	# Things to get, oh, information.
	def head(self):
		return self.admin['head']
	# This just returns the straight line versions from the head
	# downwards.
	def revs(self):
		rl = []
		rev = self.head()
		while rev:
			rl.insert(0, rev)
			rev = self.deltas[rev]['next']
		return rl
	def contents(self):
		return self.deltas[self.head()]['text']
	# This returns a tuple of (author, date)
	def revinfo(self, rev):
		rv = self.deltas[rev]
		return (rv['author'], rv['date'])
	# this does (rev, author, date) for all revs in the revs() return.
	# it turns out that desiring this is not uncommon.
	def fullrevs(self):
		rl = []
		rev = self.head()
		while rev:
			rv = self.deltas[rev]
			rl.insert(0, (rev, rv['author'], rv['date']))
			rev = rv['next']
		return rl

	# Returns who has a given version locked.
	def wholocked(self, rev):
		rls = ':'+rev
		if 'locks' not in self.admin:
			return None
		for lks in self.admin['locks']:
			if lks.endswith(rls):
				return lks[:-len(rls)]
		return None
	# Is the head locked by anyone?
	def headlocked(self):
		return self.wholocked(self.head()) != None

def parse(fp, fname):
	return RCSFile(runparse(fp), fname)
def rcsfile(fname):
	try:
		return parse(open(fname, "r"), fname)
	except (EnvironmentError, RCSErr):
		return None


# Is some contents consistent with the HEAD version of an RCS file?
# We must exterminate the RCS keyword headers if they exist.
# TODO: do this only if keyword expansion is not disabled.
crushre = re.compile(r"\$(?:Author|Date|Header|Id|Locker|Log|Name|RCSfile|Revision|Source|State)[^$]*\$")
def consistent(rfo, contents):
	c1 = crushre.sub("$RCS-goo$", contents)
	c2 = crushre.sub("$RCS-goo$", rfo.contents())
	return c1 == c2

#
# File routines.

# What is the RCS filename for a given filename?
def rcsdirname(fname):
	(head, tail) = os.path.split(fname)
	return os.path.join(head, "RCS", tail+",v")
def rcsbasename(fname):
	return fname + ",v"
	
def rcsfor(fname):
	rt = rcsbasename(fname)
	if os.path.exists(rt):
		return rt
	rt = rcsdirname(fname)
	if os.path.exists(rt):
		return rt
	# Can't find one, bye.
	return None

def rcsfname(fname):
	if fname.endswith(",v"):
		return fname
	return rcsfor(fname)
