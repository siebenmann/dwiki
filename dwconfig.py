#
# Configuration handling that's specific to dwiki.
import os
import time

# My hate is burning department of hate.
# FIXME: there should be a better way than this.
__pychecker__ = "no-shadowbuiltin no-local"
from optparse import OptionParser
__pychecker__ = ""

import config, derrors
import storage, model, htmlview, httpcore, wsgicore

# We modify the configuration object somewhat to add more requirements.
# In particular, we need 'rooturl'.
isPosInt = ('blog-display-howmany', 'atomfeed-display-howmany',
	    'feed-max-size', 'render-heuristic-ttl', 'bfc-cache-ttl',
	    'bfc-atom-ttl', 'bfc-atom-nocond-ttl',
	    'imc-cache-entries', 'imc-cache-ttl', 'imc-resp-max-size',)
isPosFloat = ('bfc-time-min', 'bfc-time-triv', 'bfc-load-min',
	      'slow-requests-by', )
timeFmts = ('%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M',
	    '%Y-%m-%d', '%Y/%m/%d',)
class SimpleDWConf(config.Configuration):
	wordlist_items = ( "bad-robots", "banned-robots", "bfc-skip-robots",
			   "literal-words", )
	list_items = ('atomfeed-virt-only-in', 'atomfeed-virt-only-adv',
		      'canon-hosts', )
	ip_ranges = ('banned-ips', 'banned-comment-ips',
		     'feed-max-size-ips', )
	
	def __init__(self):
		super(SimpleDWConf, self).__init__()
		self.must_exist.append("rooturl")

	def setDefault(self, cv, val):
		if cv not in self:
			self[cv] = val

	# True if the value is an integer timestamp or a timestamp in
	# Atom timestamp format.
	def isIntOrTimestr(self, what):
		self.hasvalue(what)
		try:
			res = int(self[what])
			if res < 0:
				raise derrors.CfgErr, "for setting %s: value is negative: %d" % (what, res)
			self[what] = res
			return
		except ValueError:
			pass
		for tf in timeFmts:
			try :
				r = time.strptime(self[what], tf)
				self[what] = time.mktime(r)
				return
			except ValueError:
				pass
		raise derrors.CfgErr, "for setting %s: value is neither an integer not a recognizable timestamp: '%s'" % (what, self[what])

	def checkGoodConfig(self):
		super(SimpleDWConf, self).checkGoodConfig()
		if 'staticurl' in self:
			self.hasvalue('staticurl')
			self.isabsdir("staticdir")
		if 'staticurl' in self:
			if self['staticurl'][0] != '/':
				self['staticurl'] = os.path.join(self['rooturl'], self['staticurl'])
		if 'comments-on' in self:
			self.isabsdir("commentsdir")

		for cv in isPosInt:
			if cv in self:
				self.isposint(cv)
		for cv in isPosFloat:
			if cv in self:
				self.isposfloat(cv)

		# feed-max-size is in kilobytes, so multiply to get true
		# bytes, unless it looks like we've already multiplied.
		if 'feed-max-size' in self and \
		   self['feed-max-size'] < 2048:
			self['feed-max-size'] = self['feed-max-size'] * 1024
		if 'feed-max-size-ips' in self:
			self.hasvalue('feed-max-size')

		# This is somewhat of a hack.
		if 'global-authseed-file' in self:
			self.loadAuthSeed()
		if 'remap-normal-to-showcomments' in self:
			self['comments-in-normal'] = True

		if 'bfc-cache-ttl' in self:
			self.isabsdir('cachedir')
			self.setDefault('bfc-time-min', 0.75)
			self.setDefault('bfc-time-triv', 0.09)

		if 'render-cache' in self:
			self.isabsdir('cachedir')

		if 'imc-resp-max-size' in self and \
		   self['imc-resp-max-size'] < 4096:
			self['imc-resp-max-size'] = self['imx-resp-max-size'] * 1024
		if 'imc-cache-entries' in self:
			self.hasvalue('imc-cache-ttl')
			self.setDefault('imc-resp-max-size', 256*1024)
		
		if 'feed-start-time' in self and \
		   not isinstance(self['feed-start-time'], int):
			self.isIntOrTimestr('feed-start-time')
		# GORY HACK ALERT
		if 'atomfeed-tag-time' in self and \
		   not isinstance(self['atomfeed-tag-time'], int):
			self.isIntOrTimestr('atomfeed-tag-time')

		# canonicalize all wordlist cases.
		for lst in self.wordlist_items:
			if lst not in self:
				continue
			self.hasvalue(lst)
			v = self[lst]
			if not isinstance(v, list):
				self[lst] = [x.strip() for x in v.split(" | ")]

		# ditto for the simpler list_items:
		for lst in self.list_items:
			if lst not in self:
				continue
			self.hasvalue(lst)
			v = self[lst]
			if not isinstance(v, list):
				self[lst] = v.split()

	def loadAuthSeed(self):
		fname = self['global-authseed-file']
		try:
			fp = open(fname, 'r')
			self['global-authseed'] = fp.read()
			fp.close()
		except EnvironmentError, e:
			raise derrors.CfgErr, "cannot read global-authseed-file '%s': %s" % (fname, str(e))

def setup_options(usage, version):
	parser = OptionParser(usage=usage, version=version)
	parser.add_option('-T', '--dump-times', dest="dumptime",
			  action="store_true", help="dump request times")
	parser.add_option('-A', '--dump-atom', dest="dumpatom",
			  action="store_true",
			  help="dump Atom conditional get information")
	parser.add_option('-C', '--config-option', dest='extraconfig',
			  action="append", metavar="OPT",
			  help="add/override a config option, with OPT in the form NAME:VALUE or just NAME.")
	parser.add_option('-D', '--delete-option', dest='rmconfig',
			  action='append', metavar="OPT",
			  help="remove OPT from the configuration.")
	parser.add_option('', '--no-cache', dest='noCache',
			  action="store_true", help="disable any caches.")
	parser.add_option('', '--stamp', dest='stampMsgs',
			  action="store_true",
			  help="add timestamp and originator IP address to messages from -T and -A.")
	parser.set_defaults(dumptime=False, dumpatom=False, extraconfig = [],
			    rmconfig = [], noCache = False, stampMsgs = False)
	return parser

def handle_options(cfg, options):
	# hack alert:
	if options.dumptime:
		cfg['dump-req-times'] = True
	if options.dumpatom:
		cfg['dump-atom-reqs'] = True
	if options.stampMsgs:
		cfg['stamp-messages'] = True

	# Bonus hack for -C
	if options.extraconfig:
		for os in options.extraconfig:
			if ':' not in os:
				cfg[os] = True
			else:
				on, ov = os.split(':', 1)
				cfg[on] = ov
	# Ditto for -D, which is at least simpler.
	if options.rmconfig:
		for os in options.rmconfig:
			cfg.rm(os)
	# Forcefully disable caching.
	if options.noCache:
		cfg.rm("cachedir")
		cfg.rm("bfc-cache-ttl")
		cfg.rm("render-cache")
	# We rely on being able to rerun checkGoodConfig(), which may
	# take some work in the underlying system.
	cfg.checkGoodConfig()

# We might as well do this centrally too, since everyone duplicates
# the code anyways.
def genR(root):
	return {'dirroot': root}
def materialize(cfgfile, options, stype = 'unset'):
	cfg = config.load(cfgfile, SimpleDWConf())
	handle_options(cfg, options)

	# Record WSGI server type in the configuration for later use
	# in some circumstances.
	cfg['wsgi-server-type'] = stype

	ms = model.Model(cfg)
	webs = htmlview.WebServices(cfg, ms)
	staticstore = None
	cachestore = None
	if 'staticurl' in cfg:
		staticstore = storage.StoragePool(genR(cfg['staticdir']))
	if 'cachedir' in cfg:
		cachestore = storage.CacheStoragePool(genR(cfg['cachedir']))

	# Create the WSGI application.
	procfunc = wsgicore.genWSGITop(httpcore.genDwikiStack(cfg),
				       cfg, ms, webs, staticstore,
				       cachestore)
	return procfunc, (cfg, ms, webs, staticstore, cachestore)
