#
# Our storage layer.
# We have one or more storage pools. Storage pools are used to get objects,
# which are named by Unix-format paths relative to the root of the storage
# area.
# Storage objects tell us four pieces of information: does this object
# exist, is it displayable, what is its contents (if it is displayable)
# and what is its history (and if history is available).
# Storage pools return None on get()s if something bad is going on.
# Otherwise they return objects, even for things that don't exist.
# Storage retrieval is case-sensitive.
from __future__ import with_statement

import rcsfile
import os, os.path, pwd, stat
import hashlib
import time, cPickle, tempfile

import derrors, utils

#
# It is worth optimizing stat() calls.
def fillStat(fname):
	try:
		return os.lstat(fname)
	except EnvironmentError:
		return None
def stat_isdir(st):
	return st and stat.S_ISDIR(st.st_mode)
def stat_islink(st):
	return st and stat.S_ISLNK(st.st_mode)

def join2(a, b):
	return a + os.path.sep + b

# Implicitly, a plain file object is not displayable if it has no contents
# at all.
sBad, sInconsist, sGood, sLocked, sNoRCS = range(5)
noStat = object()
class FileObj(object):
	type = "file"
	def __init__(self, fname, st = noStat):
		# fname is fully resolvable
		if st is noStat:
			st = fillStat(fname)
		self.real = bool(st)
		self.name = fname
		self.fstore = None
		self._state = None
		self._st = st

	# self._state insures we only try to fill once.
	def fill(self):
		if not self.real or self._state != None:
			return
		try:
			with open(self.name, "r") as fp:
				self.fstore = fp.read()
			self._state = sGood
		except EnvironmentError:
			self._state = sBad

	# We are displayable if we have contents.
	# We exist if we are real.
	def displayable(self):
		self.fill()
		return bool(self.fstore)
	def exists(self):
		return self.real
	def inconsistent(self):
		return False

	def contents(self):
		self.fill()
		return self.fstore

	# Non-VCS files have no history.
	def history(self):
		return None
	def hashistory(self):
		return False

	# But everyone has modification times.
	# (This remains valid for RCS files too, because we take the
	#  content from the raw file not the RCS file, so we take the
	#  timestamp from there too.)
	def timestamp(self):
		if self.real:
			return self._st.st_mtime
		else:
			return None
	# This is a crappy name for the ctime.
	def modstamp(self):
		if self.real:
			return self._st.st_ctime
		else:
			return None

	# The owner of a normal file is a pretty simple idea in
	# theory, but kind of annoying to implement in practice.
	# FIXME: pwd.getpwuid() is a hot path for Atom feed generation
	# and should be cacheable somehow.
	def owner(self):
		if not self.real:
			return None
		st = self._st
		try:
			pwe = pwd.getpwuid(st.st_uid)
			return pwe.pw_name
		except KeyError:
			return "#%d" % st.st_uid

	# A file's identity is its (st_dev, st_ino) combination.
	# This should be treated as an opaque identifier by all
	# callers.
	# Note that this is not persistent over the long term;
	# it is strictly useful to detect hardlinked files in
	# the short term (one processing pass).
	def identity(self):
		if not self.real:
			return None
		st = self._st
		return (st.st_dev, st.st_ino)

	# We might as well expose these directly on the page,
	# instead of forcing people into peculiar fishing expeditions
	# through the page store. Among other things, this means that
	# we don't have to tediously revalidate the path.
	def is_link(self):
		if not self.real:
			return False
		else:
			return stat_islink(self._st)
	def linkval(self):
		if not self.is_link():
			return None
		return os.readlink(self.name)

# RCS file objects treat the non-RCS file as a cache for the real
# file, which is in RCS. They only exist if both the cache file
# and the real file exist. (This is debateable, but I prefer it
# this way -- you can pull the checked out version to invalidate
# a file.)
# As a bonus, if there is no RCS file the file is still real.
# This turns out to be way what I want in practice.
class RCSFileObj(FileObj):
	def __init__(self, fname, rname, st = None):
		FileObj.__init__(self, fname, st)
		self.rname = rname
		self.rfo = None
		self._state = None

	def fill(self):
		if not self.real or self._state != None:
			return
		# At this point, self.rfo and self.fstore should be
		# empty.
		self.rfo = rcsfile.rcsfile(self.rname)
		try:
			with open(self.name, "r") as fp:
				self.fstore = fp.read()
		except EnvironmentError:
			pass

		# Now we check consistency.
		# First: real file must exist:
		if not self.fstore:
			self._state = sBad
		elif not self.rfo:
			self._state = sNoRCS
		elif self.rfo.headlocked():
			self._state = sLocked
		# If head is unlocked, the on-disk file must be the
		# same as the RCS file.
		elif not rcsfile.consistent(self.rfo, self.fstore):
			self._state = sInconsist
			# Mark us as non-displayable:
			self.fstore = None
		else:
			self._state = sGood

	def inconsistent(self):
		return self._state == sInconsist

	def history(self):
		if not self.displayable() or self._state == sNoRCS:
			return None
		# We want revision history in newest to oldest.
		revs = self.rfo.fullrevs()
		revs.reverse()
		return revs
	def hashistory(self):
		return self.displayable() and self._state != sNoRCS
	def isdirty(self):
		if self._state == sInconsist:
			return True
		elif self._state in (sBad, sGood, sNoRCS):
			return False
		else:
			assert(self._state == sLocked)
			return not rcsfile.consistent(self.rfo, self.fstore)

	def current_user(self):
		if not self.hashistory():
			return None
		return self.rfo.wholocked(self.rfo.head())
	def histtimestamp(self):
		if self._state not in (sBad, sNoRCS):
			return os.path.getmtime(self.rname)
		else:
			return None

	# Ownership is fun! For the whole family!
	# If we have RCS, our owner is either the current locker or
	# the person who checked in the head version.
	def owner(self):
		if not self.hashistory():
			return super(RCSFileObj, self).owner()
		hver = self.rfo.head()
		lck = self.rfo.wholocked(hver)
		if lck:
			return lck
		return self.rfo.revinfo(hver)[0]

# Directories have as contents a list of their constituent bits,
# minus things that start with a dot and minus directories called
# RCS.
class DirObj(FileObj):
	type = "dir"
	def fill(self):
		if self._state != None:
			return
		try:
			self.fstore = [x for x in os.listdir(self.name)
				       if utils.good_path_elem(x)]
			self.fstore.sort()
			self._state = sGood
		except EnvironmentError:
			self._state = sBad

	# We must override this because we want an empty directory to
	# be good, not bad.
	def displayable(self):
		self.fill()
		return self.fstore is not None

# This gives us the root directory for data and whether or not we have
# a separate directory for version control stuff.

# We make calls to a StoragePool to retrieve files from it, which
# gives us FileObjs, which we then mangle in various ways.
class StoragePool:
	def __init__(self, cfginfo):
		self.root = cfginfo['dirroot']
		self.cache = {}
		self.stcache = {}
		self.cache_on = True

	# To be valid, something must be a good path and every piece
	# of it must be a good name.
	def validname(self, relname):
		if relname == '':
			return True
		return utils.goodpath(relname)

	def get(self, relname, missIsNone = False):
		# We don't need to revalidate the damn name if it's
		# already in our cache.
		if relname in self.cache:
			return self.cache[relname]
		if not self.validname(relname):
			return None
		bn = join2(self.root, relname)
		st = self.getStat(bn)
		# It is more convenient to some callers if we can
		# return a None when no page exists than if we go
		# to the work of returning a real file page object.
		if not st and missIsNone:
			return None
		elif stat_isdir(st):
			res = self.fromdir(relname, bn, st)
		else:
			res = self.fromfile(relname, bn, st)
		if self.cache_on:
			self.cache[relname] = res
		return res
	
	def flush(self):
		self.cache.clear()
		self.stcache.clear()
	# When the cache is off, nothing new is added to it.
	def set_cache(self, state):
		self.cache_on = state

	# INTERNAL INTERFACE: takes a full path name, not a relative
	# name. This is dangerous.
	def getStat(self, fname):
		if fname in self.stcache:
			return self.stcache[fname]
		st = fillStat(fname)
		if self.cache_on:
			self.stcache[fname] = st
		return st

	# Get the type of a name as a string (or None if it doesn't exist).
	# The name is *relative*.
	def get_type(self, relname):
		fname = join2(self.root, relname)
		st = self.getStat(fname)
		if not st:
			return None
		elif stat_isdir(st):
			return "dir"
		else:
			return "file"

	def fromdir(self, relname, fname, st):
		__pychecker__ = "no-argsused"
		return DirObj(fname, st)
	
	def fromfile(self, relname, fname, st):
		__pychecker__ = "no-argsused"
		return FileObj(fname, st)

	# This returns a list of (modtime, filename) pairs in
	# no particular order. (You get to sort them if you
	# want to.)
	# The files may or may not be valid by the time you
	# get around to looking at them.
	# We list files, not directories, because directories
	# are just an organizational structure.
	def children(self, path):
		if not utils.goodpath(path):
			# With an iterator, this yields de nada
			return
		if path == '':
			spath = self.root
		else:
			spath = join2(self.root, path)
		pdirs = [spath,]
		rlen = len(self.root)+1
		while pdirs:
			wd = pdirs.pop()
			try:
				names = os.listdir(wd)
			except EnvironmentError:
				continue
			for name in names:
				if not utils.good_path_elem(name):
					continue
				cn = join2(wd, name)
				st = os.lstat(cn)
				self.stcache[cn] = st
				if stat.S_ISDIR(st.st_mode):
					pdirs.append(cn)
				elif stat.S_ISREG(st.st_mode):
					cn = cn[rlen:]
					# cn is guaranteed to be non-null.
					# we can only get here if it's a
					# regular file, and if it's a
					# regular file it must be inside
					# the root, which is a directory.
					yield (st.st_mtime, cn)
		# and we're done

	# Note that unlike other checks, this checks only that the
	# path is good not that the names in the path are good.
	# It can thus be used to probe for the existence of
	# names that cannot be shown.
	def exists(self, relname):
		# It is worth fast-casing this, because it avoids
		# an unnecessary bogusname check.
		jn = join2(self.root, relname)
		if jn in self.stcache:
			return bool(self.stcache[jn])
		if utils.boguspath(relname):
			return False
		else:
			st = self.getStat(jn)
			return bool(st)

	def linkval(self, path):
		if not utils.goodpath(path):
			return None
		fpath = join2(self.root, path)
		st = self.getStat(fpath)
		if not stat_islink(st):
			return None
		return os.readlink(fpath)

# There are two variants of RCS storage pools.
# If given a 'rcsroot', RCS files are found in a parallel hierarchy
# (with no RCS/ directories, just with ,v on the end).
# Otherwise they are found in RCS/ directories in the regular root.
class RCSStoragePool(StoragePool):
	def __init__(self, cfginfo):
		StoragePool.__init__(self, cfginfo)
		if 'rcsdir' in cfginfo:
			self.rroot = cfginfo['rcsdir']
		else:
			self.rroot = None

	def fromfile(self, relname, fname, st):
		if self.rroot:
			rname = join2(self.rroot, rcsfile.rcsbasename(relname))
		else:
			rname = join2(self.root, rcsfile.rcsdirname(relname))
		#fname = join2(self.root, relname)
		return RCSFileObj(fname, rname, st)

# Make a directory in the face of races.
def makedirs(name):
	head, tail = os.path.split(name)
	if not tail:
		head, tail = os.path.split(head)
	if head and tail and not os.path.exists(head):
		makedirs(head)
	try:
		os.mkdir(name)
	except EnvironmentError:
		if not os.path.isdir(name):
			raise

#
# Store comments in a read/write pool.
class CommentStoragePool(StoragePool):
	def blobname(self, blob):
		h = hashlib.sha1()
		h.update(blob)
		return h.hexdigest()
	
	# The biggest and most complex operation is writing something
	# new to the store.
	# New blobs are stored in directories, which we make as necessary.
	# They are named by the SHA1 of their contents, in hex form.
	# FIXME: come up with a better encoding scheme.
	# FIXME: come up with some better way of returning errors.
	def newblob(self, where, blobstr):
		if not self.validname(where):
			raise derrors.IntErr, "bad commentstore name: '%s'" % where
		loc = join2(self.root, where)
		try:
			if not os.path.isdir(loc):
				makedirs(loc)
		except EnvironmentError, e:
			raise derrors.IOErr, "could not make directory '%s': %s" % (loc, str(e))
		objname = self.blobname(blobstr)
		pname = join2(loc, objname)
		phase = "create"
		try:
			fd = os.open(pname, os.O_CREAT|os.O_EXCL|os.O_WRONLY,
				     0644)
			phase = "write"
			os.write(fd, blobstr)
			os.close(fd)
		except EnvironmentError, e:
			# It exists already, so we 'succeeded' as it were.
			if phase == "create" and os.path.exists(pname):
				return True 
			raise derrors.IOErr, "could not %s file '%s': %s" % (phase, pname, str(e))
		return True


#
# Cache storage is also a read/write pool. Like comments, the interface
# is deliberately restricted.
#
# The cache is addressed by a zone / host / path / key quad. Zone and key
# are single path elements; path is a valid regular path. To make sure
# we can never collide with valid sub-paths, the keys always have a
# '~' appended to the end internally.
#
# Cache cleaning is explicitly not addressed by this cache storage
# module. The author considers unlink() dangerous; the rename() that
# the store code does is already bad enough. Clean the cache outside
# of dwiki with your favorite mechanism.
class CacheStoragePool(StoragePool):
	def validate_quad(self, zone, host, path, key):
		if not self.validname(path) or \
		   '/' in key or '/' in zone or '/' in host or \
		   not utils.good_path_elem(host) or \
		   not utils.good_path_elem(zone) or \
		   not utils.good_path_elem(key):
			raise derrors.CacheKeyErr, "bad zone / host / path / key in cachestore: '%s' '%s' '%s' '%s'" % (zone, host, path, key)

	def get(self, relname, missIsNone = False):
		__pychecker__ = "no-argsused"
		raise derrors.IntErr, "using get on a cachestore"

	def fetch(self, zone, host, path, key = "default", TTL = None):
		self.validate_quad(zone, host, path, key)
		key = key + '~'
		fname = os.path.sep.join([self.root, zone, host, path, key])
		st = self.getStat(fname)
		now = time.time()
		if not st:
			return None
		elif not stat.S_ISREG(st.st_mode):
			raise derrors.IntErr, "not a regular file in cachestore: '%s' '%s' '%s' '%s'" % (zone, host, path, key)
		elif TTL and (st.st_mtime + TTL) < now:
			return None

		# Finally we can get and retrieve the cached file.
		# Cached file objects are always depickled before
		# getting returned. (We use FileObjs because they
		# handle a number of details for us.)
		#
		# Rather than cache the depickled objects, which may
		# be mutated by later people, we cache the file object
		# data and depickle each time.
		if fname not in self.cache:
			fo = FileObj(fname, st)
			data = fo.contents()
		else:
			data = self.cache[fname]

		if not data:
			# This might be: empty file, no-permissions file,
			# and probably others. None are severe enough to
			# kill us.
			return None

		ro = cPickle.loads(data)
		if self.cache_on:
			self.cache[fname] = data
		return ro

	#
	# Storage is complicated by our desire to be atomic, or
	# something close to it, so we don't overwrite the file
	# in place but instead make a temporary one and then rename
	# to the target name.
	def store(self, value, zone, host, path, key = "default"):
		self.validate_quad(zone, host, path, key)
		key = key + '~'
		dname = os.path.sep.join([self.root, zone, host, path])
		fname = join2(dname, key)

		# Serialize data. We tell dumps to use the best format
		# it has, since we don't care about our caches being
		# read by an older version.
		data = cPickle.dumps(value, -1)

		# Make the directory, which is zone + path.
		try:
			if not os.path.isdir(dname):
				makedirs(dname)
		except EnvironmentError, e:
			return "could not make directory '%s': %s" % (dname, str(e))

		# We write the data by a) making a tempfile,
		# b) writing the data to it, c) renaming the
		# tempfile to the real name.
		# Arguably failing to make the cache file should not
		# be a fatal error.
		try:
			stage = "making tempfile"
			(fd, tname) = tempfile.mkstemp("XXXX~", ".store",
						       dname)
			stage = "writing data"
			os.write(fd, data)
			os.close(fd)
			# Personal twitch:
			stage = "fixing permissions"
			os.chmod(tname, 0644)
			stage = "renaming to final name"
			os.rename(tname, fname)
			return None
		except EnvironmentError, e:
			return "error while %s for %s/%s/%s: %s" % (stage, zone, path, key, str(e))

	# Return the timestamp of a cache entry or None if there is no such
	# cache entry. This enables flag file based cache invalidation, or
	# more exactly flag-quad based invalidation; store to the quad to
	# update its time, then check the relatively time of the flag quad
	# and your cache data.
	def timeof(self, zone, host, path, key = "default"):
		self.validate_quad(zone, host, path, key)
		key = key + '~'
		dname = os.path.sep.join([self.root, zone, host, path])
		fname = join2(dname, key)
		st = self.getStat(fname)
		if not st:
			return None
		elif not stat.S_ISREG(st.st_mode):
			raise derrors.IntErr, "not a regular file in cachestore: '%s' '%s' '%s' '%s'" % (zone, host, path, key)
		else:
			return st.st_mtime
