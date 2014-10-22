#
# Configuration is deliberately low-rent.
#
# There is a file of 'name value' pairs, with empty lines and comments
# allowed, and we just read that and return a dictionary with all the
# information. 'value' may be omitted, in which case it becomes True.
# FIXME: no longer so low-rent. Document better.
#
import os.path
import netblock
import derrors
import contread

class Configuration(object):
	# This is a list so that it can be extended by subclasses.
	must_exist = ['pagedir', 'tmpldir', 'wikiname',]
	canon_dirs = { 'pagedir': 'pages', 'tmpldir': 'templates',
		       'rcsdir': 'rcsroot' }
	wordlist_items = ()
	list_items = ()
	ip_ranges = ()
	
	def __init__(self):
		super(Configuration, self).__init__()
		self.kv = {}

	# I wanted to get away with __getattr__, but noooo.
	# My life sucks. Fake enough of dictionaries to make
	# us happy.
	def __contains__(self, item):
		return item in self.kv
	def __getitem__(self, key):
		return self.kv[key]
	def __setitem__(self, key, val):
		self.kv[key] = val
	def __len__(self):
		return len(self.kv)
	def keys(self):
		return self.kv.keys()
	def get(self, key, default = None):
		return self.kv.get(key, default)
	def rm(self, key):
		if key in self.kv:
			del self.kv[key]

	# add an item to a list.
	def addWordListItem(self, key, val):
		if key in self.kv:
			self.kv[key] = self.kv[key] + " | " + val
		else:
			self.kv[key] = val
	def addListItem(self, key, val):
		if key in self.kv:
			self.kv[key] = self.kv[key] + " " + val
		else:
			self.kv[key] = val
	def addIpRanges(self, key, val):
		if key not in self.kv:
			self.kv[key] = netblock.IPRanges()
		for ipr in val.split():
			self.kv[key].add(ipr)

	# Verification functions.
	def hasvalue(self, what):
		if what not in self:
			raise derrors.CfgErr("setting %s is not specified" % what)
		if self[what] is True:
			raise derrors.CfgErr("for setting %s: no value specified" % what)
	def isabspath(self, what):
		self.hasvalue(what)
		if self[what][0] != '/':
			raise derrors.CfgErr("for setting %s: path '%s' is not an absolute path" % (what, self[what]))

	def isabsdir(self, what):
		self.isabspath(what)
		if not os.path.isdir(self[what]):
			raise derrors.CfgErr("for setting %s: no directory '%s'" % (what, self[what]))
	def isabsfile(self, what):
		self.isabspath(what)
		if not os.path.isfile(self[what]):
			raise derrors.CfgErr("for setting %s: no file '%s'" % (what, self[what]))

	def isposint(self, what):
		self.hasvalue(what)
		try:
			res = int(self[what])
			if res <= 0:
				raise derrors.CfgErr("for setting %s: value is zero or negative: %d" % (what, res))
			self[what] = res
		except ValueError:
			raise derrors.CfgErr("for setting %s: value is not an integer: '%s'" % (what, self[what]))
	def isposfloat(self, what):
		self.hasvalue(what)
		try:
			res = float(self[what])
			if res <= 0:
				raise derrors.CfgErr("for setting %s: value is zero or negative: %f" % (what, res))
			self[what] = res
		except ValueError:
			raise derrors.CfgErr("for setting %s: value is not a float: '%s'" % (what, self[what]))

	def mustDiffer(self, one, two):
		if self[one] == self[two]:
			raise derrors.CfgErr("for setting %s and %s: same value '%s'" % (one, two, self[one]))

	# -- try to canonically create certain entries if they exist.
	def inventNormals(self):
		if 'root' not in self:
			return
		for i in self.canon_dirs.keys():
			if i in self:
				return
			tp = os.path.join(self['root'], self.canon_dirs[i])
			if os.path.isdir(tp):
				self[i] = tp

	# --- try to canonicalize any apparent directories.
	def tryCanonObj(self, what, verfunc):
		if 'root' not in self:
			return
		self.hasvalue(what)
		if self[what][0] == '/':
			return
		tp = os.path.join(self['root'], self[what])
		if verfunc(tp):
			self[what] = tp
	def tryCanonDir(self, what):
		self.tryCanonObj(what, os.path.isdir)
	def tryCanonFile(self, what):
		self.tryCanonObj(what, os.path.isfile)

	# --- for everything in the config, if its name ends with 'dir'
	# or 'file', try to canonicalize it.
	def canonConfigPaths(self):
		if 'root' not in self:
			return
		for i in self.kv.keys():
			if i.endswith("dir") and self[i] is not True:
				self.tryCanonDir(i)
			if i.endswith("file") and self[i] is not True:
				self.tryCanonFile(i)

	# Okay, this is what really does all the work.
	def checkGoodConfig(self):
		if 'root' in self:
			self.isabsdir('root')
		self.canonConfigPaths()
		self.inventNormals()
		for i in self.must_exist:
			self.hasvalue(i)
		# All things that are named like directories and files
		# must be there.
		for i in self.kv.keys():
			if i.endswith("dir"):
				self.isabsdir(i)
			if i.endswith("file"):
				self.isabsfile(i)

		# Cannot have some things overlapping.
		self.mustDiffer('pagedir', 'tmpldir')
		if 'rcsdir' in self:
			self.mustDiffer('pagedir', 'rcsdir')
			
		# If we've gotten here, we're good.		

# 'fp' is expected to be a contread.RFC822File() object because we don't
# trim blank lines and comment lines as we read from it.
def _loadfile(fp, cfg = None):
	if cfg is None:
		cfg = Configuration()

	for line in fp:
		# (this removes any trailing newline)
		line = line.strip()
		tl = line.split(None, 1)
		if len(tl) == 1:
			tl.append(True)
		else:
			tl[1] = tl[1].strip()
		if tl[0] in cfg.wordlist_items:
			cfg.addWordListItem(tl[0], tl[1])
		elif tl[0] in cfg.list_items:
			cfg.addListItem(tl[0], tl[1])
		elif tl[0] in cfg.ip_ranges:
			cfg.addIpRanges(tl[0], tl[1])
		elif tl[0] in cfg:
			raise derrors.CfgErr("setting %s supplied more than once" % tl[0])
		else:
			cfg[tl[0]] = tl[1]

	cfg.checkGoodConfig()
	return cfg

def load(fname, cfg = None):
	try:
		fp = contread.openfile(fname)
		res = _loadfile(fp, cfg)
		fp.close()
		return res
	except EnvironmentError as e:
		raise derrors.IOErr("could not read config file %s: %s" % (fname, str(e)))
	except contread.StartingContinuedLine as e:
		raise derrors.CfgErr("configuration file %s: %s" % (fname, str(e)))
	except derrors.CfgErr as e:
		raise derrors.CfgErr("configuration file %s: %s" % (fname, str(e)))
