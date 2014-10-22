#!/usr/bin/python
#
# Provide a convenient interface to create a dwiki user or update their
# password, one usable by anyone who can write to the password file.
#
# Usage:
#	dpasswd.py config-file user plain-passwd
#
import sys, re, os
import time, pwd

import htmlauth, derrors, config, dwconfig

pwline_re = re.compile(r'\s*([^\s]+)\s+([^\s]+)(\s|\n|$)')

def report(msg):
	print "%s: %s" % (sys.argv[0], msg)
	
def updatepw(pwf, user, pwh):
	fp = open(pwf, "a+")
	while 1:
		lpos = fp.tell()
		line = fp.readline()
		if not line:
			break
		t = line.strip()
		if not t or t[0] == '#':
			continue

		# Really, this should match always.
		mo = pwline_re.match(line)
		if not mo:
			lstr = line.rstrip("\n")
			report("Garbled line: |%s|" % lstr)
			continue

		# Is this us?
		if not mo.group(1) == user:
			continue
		# Safety: we had better fit in the same space.
		if len(pwh) != len(mo.group(2)):
			report("FATAL ERROR: pwf mismatch: %d vs %d, |%s| vs |%s|" % (len(mo.group(2)), len(pwh), mo.group(2), pwh))
			return
		if pwh == mo.group(2):
			report("New and old passwords for %s are the same; nothing to do." % user)
			return

		# Okay, we do. Seek to position, write.
		report("Updating password for %s in place." % user)
		tpos = lpos + mo.start(2)
		# How do I hate you, standard IO. We can't do this
		# through *any* stdio interface, so we have to go
		# to raw file descriptors.
		fp.close()
		fd = os.open(pwf, os.O_WRONLY|os.O_EXCL)
		os.lseek(fd, tpos, 0)
		os.write(fd, pwh)
		os.close(fd)
		return

	# Nothing found, append.
	report("No user entry for %s found, adding one." % user)
	uid = os.getuid()
	try:
		pwe = pwd.getpwuid(uid)
		uname = pwe.pw_name
	except KeyError:
		uname = "#%d" % uid
	tstr = time.strftime("%c %Z", time.localtime())
	commentline = "\n# Added %s by %s.\n" % (tstr, uname)
	pwline = "%-15s %s\n" % (user, pwh)
	fp.write(commentline + pwline)
	fp.close()

def die(msg):
	sys.stderr.write("%s: %s\n" % (sys.argv[0], msg))
	sys.exit(1)

def main(args):
	if len(args) != 3:
		sys.stderr.write("usage: dpasswd.py config-file user password\n")
		sys.exit(1)
	try:
		cfg = config.load(args[0], dwconfig.SimpleDWConf())
	except derrors.WikiErr as e:
		die("Error loading configuration: %s" % str(e))
	if 'authfile' not in cfg:
		die("Authentication is not configured for %s." % cfg['wikiname'])

	user = args[1]
	pwh = htmlauth.encryptPassword(user, args[2])
	try:
		updatepw(cfg['authfile'], user, pwh)
	except EnvironmentError as e:
		die("Error during update: %s" % str(e))

if __name__ == "__main__":
	main(sys.argv[1:])
