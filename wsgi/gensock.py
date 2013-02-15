#
# Common:
import os, socket

def gen_sock(addr, options):
	perms = None
	if options.systemd:
		s = addr
	elif len(addr) == 2:
		s = socket.socket()
	else:
		s = socket.socket(socket.AF_UNIX,
				  socket.SOCK_STREAM)
		perms = options.perms
	s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
	if not options.systemd:
		s.bind(addr)
		# socket.SOMAXCONN is not dynamically adjusted to
		# reflect the real system configuration, but it's
		# harmless to go too high.
		s.listen(max(socket.SOMAXCONN, 1024))
	if perms:
		os.chmod(addr, perms)
	return s
