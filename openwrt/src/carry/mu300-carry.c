/*
 * mu300-carry: the file work of an update that keeps everything the user made (mu300-update, the carry block;
 * docs/FINDINGS.md 85). One static program does it on every path an update takes - Ubuntu, OpenWrt's busybox, and
 * Android's toybox when an installer or the Magisk zip updates - because none of their cp and tar keep all of a file
 * (measured: busybox cp -a makes a sparse file dense and drops extended attributes and the fraction of the second;
 * toybox cp -a also breaks hard links).
 *
 *   mu300-carry scan ROOT              one line for everything below ROOT, never leaving its filesystem:
 *                                        TYPE MODE UID GID SIZE NLINK INODE BLOCKS PATH
 *                                      TYPE f d l c b p s as in a manifest, m for a directory another filesystem is
 *                                      mounted on (not entered)
 *   mu300-carry sum ROOT < PATHS       "HASH PATH" for each: sha256 of a file's content or a link's target, - for
 *                                      anything else, ! when it is not there
 *   mu300-carry manifest ROOT IMAGE TAG   the manifest of an image's files (tools/image-manifest.py has the format
 *                                      and makes the same lines from a tarball), without etc/mu300/image-manifest.gz
 *   mu300-carry copy SRC DST [IDMAP] < PATHS   make DST/PATH what SRC/PATH is: content (holes stay holes), owner,
 *                                      mode, extended attributes (capabilities, ACLs), times to the nanosecond; names
 *                                      of one file in the list stay one file; a directory is made, not filled
 *   mu300-carry link SRC DST [IDMAP] < PATHS   the same, but a file becomes a second name of SRC's (no data copied)
 *
 * PATHS: one per line, relative to the root, a backslash written \\ and a newline \n, parents before what is in
 * them. IDMAP: lines "u OLD NEW" and "g OLD NEW", owners to change on the way (an account whose id differs in the
 * new system); a file that needs it is copied even by link, and loses its set-id bits and its capability on the
 * way ("! PATH" on stdout says so). Nothing is ever written through a symbolic link of DST,
 * and nothing but a non-directory at PATH itself is replaced: a directory in the way is an error. A path that is
 * gone from SRC (a running system) is passed over and counted. Exit 0, or 1 with the reason on stderr.
 *
 * Build: gcc -O2 -static -o mu300-carry mu300-carry.c
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/types.h>
#include <sys/xattr.h>
#include <unistd.h>

#define SELF "etc/mu300/image-manifest.gz"

static void die(const char *fmt, ...)
{
	va_list ap;

	fprintf(stderr, "mu300-carry: ");
	va_start(ap, fmt);
	vfprintf(stderr, fmt, ap);
	va_end(ap);
	fputc('\n', stderr);
	exit(1);
}

static void *xmalloc(size_t n)
{
	void *p = malloc(n ? n : 1);

	if (!p)
		die("out of memory");
	return p;
}

/* ---- sha256 (FIPS 180-4) */
struct sha256 { uint32_t h[8]; uint64_t len; unsigned char buf[64]; size_t fill; };

static const uint32_t K256[64] = {
	0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98,
	0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
	0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8,
	0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
	0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819,
	0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
	0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
	0xc67178f2
};

#define ROR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))

static void sha256_block(struct sha256 *s, const unsigned char *p)
{
	uint32_t w[64], a, b, c, d, e, f, g, h, t1, t2;
	int i;

	for (i = 0; i < 16; i++)
		w[i] = (uint32_t)p[4 * i] << 24 | (uint32_t)p[4 * i + 1] << 16 | (uint32_t)p[4 * i + 2] << 8 | p[4 * i + 3];
	for (; i < 64; i++)
		w[i] = (ROR(w[i - 2], 17) ^ ROR(w[i - 2], 19) ^ (w[i - 2] >> 10)) + w[i - 7] +
		       (ROR(w[i - 15], 7) ^ ROR(w[i - 15], 18) ^ (w[i - 15] >> 3)) + w[i - 16];
	a = s->h[0]; b = s->h[1]; c = s->h[2]; d = s->h[3]; e = s->h[4]; f = s->h[5]; g = s->h[6]; h = s->h[7];
	for (i = 0; i < 64; i++) {
		t1 = h + (ROR(e, 6) ^ ROR(e, 11) ^ ROR(e, 25)) + ((e & f) ^ (~e & g)) + K256[i] + w[i];
		t2 = (ROR(a, 2) ^ ROR(a, 13) ^ ROR(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
		h = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
	}
	s->h[0] += a; s->h[1] += b; s->h[2] += c; s->h[3] += d; s->h[4] += e; s->h[5] += f; s->h[6] += g; s->h[7] += h;
}

static void sha256_init(struct sha256 *s)
{
	static const uint32_t iv[8] = { 0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c,
					0x1f83d9ab, 0x5be0cd19 };

	memcpy(s->h, iv, sizeof(iv));
	s->len = 0;
	s->fill = 0;
}

static void sha256_update(struct sha256 *s, const unsigned char *p, size_t n)
{
	s->len += n;
	while (n) {
		size_t k = 64 - s->fill;

		if (k > n)
			k = n;
		memcpy(s->buf + s->fill, p, k);
		s->fill += k; p += k; n -= k;
		if (s->fill == 64) {
			sha256_block(s, s->buf);
			s->fill = 0;
		}
	}
}

static void sha256_hex(struct sha256 *s, char out[65])
{
	uint64_t bits = s->len * 8;
	unsigned char pad[72] = { 0x80 }, len[8];
	size_t n = (s->fill < 56 ? 56 : 120) - s->fill;
	int i;

	for (i = 0; i < 8; i++)
		len[i] = bits >> (56 - 8 * i);
	sha256_update(s, pad, n);
	sha256_update(s, len, 8);
	for (i = 0; i < 8; i++)
		sprintf(out + 8 * i, "%08x", (unsigned)s->h[i]);
}

/* the hash of what PATH (under DIRFD) is: a file's content, a link's target; "-" for anything else, "!" when it is
 * not there */
static void hash_of(int dirfd, const char *path, const struct stat *st, char out[65])
{
	static unsigned char buf[1 << 16];
	struct sha256 s;
	ssize_t n;

	strcpy(out, "-");
	sha256_init(&s);
	if (S_ISREG(st->st_mode)) {
		int fd = openat(dirfd, path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);

		if (fd < 0) {
			strcpy(out, "!");
			return;
		}
		while ((n = read(fd, buf, sizeof(buf))) > 0)
			sha256_update(&s, buf, n);
		close(fd);
		if (n < 0)
			die("%s: %s", path, strerror(errno));
		sha256_hex(&s, out);
	} else if (S_ISLNK(st->st_mode)) {
		n = readlinkat(dirfd, path, (char *)buf, sizeof(buf));
		if (n < 0) {
			strcpy(out, "!");
			return;
		}
		sha256_update(&s, buf, n);
		sha256_hex(&s, out);
	}
}

/* ---- paths as the lists and manifests write them */
static void put_escaped(FILE *f, const char *p)
{
	for (; *p; p++) {
		if (*p == '\\')
			fputs("\\\\", f);
		else if (*p == '\n')
			fputs("\\n", f);
		else
			fputc(*p, f);
	}
}

static char *escaped(const char *p)
{
	char *out = xmalloc(2 * strlen(p) + 1), *o = out;

	for (; *p; p++) {
		if (*p == '\\' || *p == '\n') {
			*o++ = '\\';
			*o++ = *p == '\n' ? 'n' : '\\';
		} else {
			*o++ = *p;
		}
	}
	*o = 0;
	return out;
}

/* a line of a list, in place: the path it names. Relative, no empty, "." or ".." part: anything else is refused */
static char *unescape(char *line)
{
	char *i = line, *o = line, *part;

	for (; *i && *i != '\n'; i++) {
		if (*i == '\\' && i[1] == 'n') {
			*o++ = '\n';
			i++;
		} else if (*i == '\\' && i[1] == '\\') {
			*o++ = '\\';
			i++;
		} else {
			*o++ = *i;
		}
	}
	*o = 0;
	if (!*line || *line == '/')
		die("not a relative path: '%s'", line);
	for (part = line; part; part = strchr(part, '/') ? strchr(part, '/') + 1 : NULL) {
		size_t n = strcspn(part, "/");

		if (n == 0 || (n == 1 && part[0] == '.') || (n == 2 && part[0] == '.' && part[1] == '.'))
			die("not a plain path: '%s'", line);
	}
	return line;
}

static char type_of(mode_t m)
{
	return S_ISREG(m) ? 'f' : S_ISDIR(m) ? 'd' : S_ISLNK(m) ? 'l' : S_ISCHR(m) ? 'c' : S_ISBLK(m) ? 'b' :
	       S_ISFIFO(m) ? 'p' : S_ISSOCK(m) ? 's' : '?';
}

/* ---- scan and manifest */
struct walk {
	dev_t dev;
	int manifest;		/* manifest lines (with hashes) into lines[] instead of scan lines to stdout */
	char **lines;
	size_t n, cap;
};

static void walk_dir(struct walk *w, int fd, char **path, size_t *cap, size_t len)
{
	DIR *d = fdopendir(fd);
	struct dirent *e;

	if (!d)
		die("%s: %s", *path, strerror(errno));
	while ((errno = 0, e = readdir(d))) {
		struct stat st;
		size_t l = strlen(e->d_name);
		char t;

		if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, ".."))
			continue;
		if (len + l + 2 > *cap)
			*path = realloc(*path, *cap = 2 * (len + l + 2));
		if (!*path)
			die("out of memory");
		memcpy(*path + len, e->d_name, l + 1);
		if (fstatat(fd, e->d_name, &st, AT_SYMLINK_NOFOLLOW)) {
			if (errno == ENOENT)	/* gone while we looked: a running system */
				continue;
			die("%s: %s", *path, strerror(errno));
		}
		t = type_of(st.st_mode);
		if (t == 'd' && st.st_dev != w->dev)
			t = 'm';
		if (w->manifest) {
			char h[65], *esc, *line;

			if (!strcmp(*path, SELF))
				continue;
			if (t == 's' || t == 'm' || t == '?')
				die("%s: an image cannot hold this", *path);
			hash_of(fd, e->d_name, &st, h);
			if (h[0] == '!')
				die("%s: cannot read it", *path);
			esc = escaped(*path);
			line = xmalloc(strlen(esc) + 160);
			sprintf(line, "%c %o %u %u %llu %s %s", t, t == 'l' ? 0777 : (unsigned)(st.st_mode & 07777),
				(unsigned)st.st_uid, (unsigned)st.st_gid,
				t == 'f' || t == 'l' ? (unsigned long long)st.st_size : 0ULL, h, esc);
			free(esc);
			if (w->n == w->cap)
				w->lines = realloc(w->lines, (w->cap = w->cap ? 2 * w->cap : 4096) * sizeof(char *));
			if (!w->lines)
				die("out of memory");
			w->lines[w->n++] = line;
		} else {
			printf("%c %o %u %u %llu %lu %llu %llu ", t, (unsigned)(st.st_mode & 07777), (unsigned)st.st_uid,
			       (unsigned)st.st_gid, (unsigned long long)st.st_size, (unsigned long)st.st_nlink,
			       (unsigned long long)st.st_ino, (unsigned long long)st.st_blocks);
			put_escaped(stdout, *path);
			putchar('\n');
		}
		if (t == 'd') {
			int sub = openat(fd, e->d_name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);

			if (sub < 0) {
				if (errno == ENOENT)
					continue;
				die("%s: %s", *path, strerror(errno));
			}
			(*path)[len + l] = '/';
			(*path)[len + l + 1] = 0;
			walk_dir(w, sub, path, cap, len + l + 1);
		}
	}
	if (errno)
		die("%s: %s", *path, strerror(errno));
	closedir(d);
}

/* the path of a manifest line: after the sixth blank */
static const char *line_path(const char *l)
{
	int i;

	for (i = 0; i < 6; i++)
		l = strchr(l, ' ') + 1;
	return l;
}

static int by_path(const void *a, const void *b)
{
	return strcmp(line_path(*(char *const *)a), line_path(*(char *const *)b));
}

static int cmd_walk(const char *root, const char *image, const char *tag)
{
	struct walk w = { 0 };
	struct stat st;
	size_t cap = 4096, i;
	char *path = xmalloc(cap);
	int fd = open(root, O_RDONLY | O_DIRECTORY | O_CLOEXEC);

	if (fd < 0 || fstat(fd, &st))
		die("%s: %s", root, strerror(errno));
	w.dev = st.st_dev;
	w.manifest = image != NULL;
	path[0] = 0;
	walk_dir(&w, fd, &path, &cap, 0);
	if (w.manifest) {
		qsort(w.lines, w.n, sizeof(char *), by_path);
		printf("mu300-image-manifest 1 %s %s\n", image, tag);
		for (i = 0; i < w.n; i++)
			puts(w.lines[i]);
	}
	if (fflush(stdout) || ferror(stdout))
		die("cannot write the list");
	return 0;
}

static int cmd_sum(const char *root)
{
	char *line = NULL, h[65];
	size_t cap = 0;
	int fd = open(root, O_RDONLY | O_DIRECTORY | O_CLOEXEC);

	if (fd < 0)
		die("%s: %s", root, strerror(errno));
	while (getline(&line, &cap, stdin) > 0) {
		struct stat st;
		char *p = unescape(line);

		if (fstatat(fd, p, &st, AT_SYMLINK_NOFOLLOW))
			strcpy(h, "!");
		else
			hash_of(fd, p, &st, h);
		printf("%s ", h);
		put_escaped(stdout, p);
		putchar('\n');
	}
	if (fflush(stdout) || ferror(stdout))
		die("cannot write the list");
	return 0;
}

/* ---- copy and link */
struct idmap { char kind; unsigned from, to; };
static struct idmap *idmap;
static size_t nidmap;

static unsigned map_id(char kind, unsigned id)
{
	size_t i;

	for (i = 0; i < nidmap; i++)
		if (idmap[i].kind == kind && idmap[i].from == id)
			return idmap[i].to;
	return id;
}

static void read_idmap(const char *file)
{
	FILE *f = fopen(file, "r");
	char kind;
	unsigned from, to;

	if (!f)
		die("%s: %s", file, strerror(errno));
	while (fscanf(f, " %c %u %u", &kind, &from, &to) == 3) {
		if (kind != 'u' && kind != 'g')
			die("%s: not an id map", file);
		idmap = realloc(idmap, (nidmap + 1) * sizeof(*idmap));
		if (!idmap)
			die("out of memory");
		idmap[nidmap++] = (struct idmap){ kind, from, to };
	}
	fclose(f);
}

static int lax;		/* not root (the tests): owners and the attributes only root may set are left out */

/* owner, mode, extended attributes and times of SRC's NAME onto DST's NAME, in the order that keeps them: a chown
 * clears set-id bits and capabilities, so it comes first. The attribute calls take a path: /proc/self/fd/N/NAME,
 * below the directory that was opened without following a link, and never through a link at NAME itself. */
static void copy_meta(int sfd, const char *sname, int dfd, const char *dname, const struct stat *st, const char *path)
{
	struct timespec ts[2] = { st->st_atim, st->st_mtim };
	char sp[320], dp[320], *names, *n;
	ssize_t len, vlen;
	uid_t uid = map_id('u', st->st_uid);
	gid_t gid = map_id('g', st->st_gid);
	/* The owner gets another number (IDMAP): what gives the file the rights of its owner does not go along - the
	 * set-user-id and set-group-id bits, a file capability. Said on stdout ("! PATH") when there was one. */
	int moved = uid != st->st_uid || gid != st->st_gid, took = 0;
	mode_t mode = st->st_mode & 07777;

	if (moved && (mode & 06000)) {
		mode &= ~06000;
		took = 1;
	}
	if (fchownat(dfd, dname, uid, gid, AT_SYMLINK_NOFOLLOW) && !lax)
		die("%s: owner: %s", path, strerror(errno));
	if (!S_ISLNK(st->st_mode) && fchmodat(dfd, dname, mode, 0))
		die("%s: mode: %s", path, strerror(errno));
	snprintf(sp, sizeof(sp), "/proc/self/fd/%d/%s", sfd, sname);
	snprintf(dp, sizeof(dp), "/proc/self/fd/%d/%s", dfd, dname);
	len = llistxattr(sp, NULL, 0);
	if (len < 0 && errno != ENOTSUP && errno != ENODATA)
		die("%s: attributes: %s%s", path, strerror(errno), errno == ENOENT ? " (is /proc mounted?)" : "");
	if (len > 0) {
		names = xmalloc(len);
		len = llistxattr(sp, names, len);
		for (n = names; len > 0 && n < names + len; n += strlen(n) + 1) {
			char *val;

			if (moved && !strcmp(n, "security.capability")) {
				took = 1;
				continue;
			}
			vlen = lgetxattr(sp, n, NULL, 0);
			if (vlen < 0)
				die("%s: attribute %s: %s", path, n, strerror(errno));
			val = xmalloc(vlen);
			vlen = lgetxattr(sp, n, val, vlen);
			/* (a label of Android's SELinux that this kernel will not take is left out: the systems
			 * have no SELinux) */
			if (vlen < 0 || (lsetxattr(dp, n, val, vlen, 0) && strcmp(n, "security.selinux") &&
					 !(lax && (errno == EPERM || errno == ENOTSUP || errno == EACCES))))
				die("%s: attribute %s: %s", path, n, strerror(errno));
			free(val);
		}
		free(names);
	}
	if (utimensat(dfd, dname, ts, AT_SYMLINK_NOFOLLOW))
		die("%s: times: %s", path, strerror(errno));
	if (took) {
		fputs("! ", stdout);
		put_escaped(stdout, path);
		putchar('\n');
	}
}
/* the content of SRC's file into a new file, holes as holes; GONE when the file went away meanwhile */
#define GONE (~0ULL)
static unsigned long long copy_data(int sfd, const char *sname, int dfd, const char *dname, const struct stat *st,
				    const char *path)
{
	static char buf[1 << 20];
	int in = openat(sfd, sname, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
	int out;
	off_t pos = 0, end;
	unsigned long long done = 0;
	int holes = 1;

	if (in < 0 && errno == ENOENT)
		return GONE;
	if (in < 0)
		die("%s: %s", path, strerror(errno));
	out = openat(dfd, dname, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
	if (out < 0)
		die("%s: %s", path, strerror(errno));
	while (pos < st->st_size) {
		off_t data = holes ? lseek(in, pos, SEEK_DATA) : pos;

		if (data < 0) {
			if (errno == ENXIO)	/* a hole to the end */
				break;
			holes = 0;		/* a filesystem that cannot say: every byte is read */
			data = pos;
		}
		end = holes ? lseek(in, data, SEEK_HOLE) : st->st_size;
		if (end < 0)
			end = st->st_size;
		if (lseek(in, data, SEEK_SET) < 0 || lseek(out, data, SEEK_SET) < 0)
			die("%s: %s", path, strerror(errno));
		for (pos = data; pos < end;) {
			size_t want = (size_t)(end - pos) < sizeof(buf) ? (size_t)(end - pos) : sizeof(buf);
			ssize_t n = read(in, buf, want), w, off;

			if (n < 0)
				die("%s: read: %s", path, strerror(errno));
			if (n == 0) {		/* it shrank while we read */
				pos = st->st_size;
				break;
			}
			for (off = 0; off < n; off += w) {
				w = write(out, buf + off, n - off);
				if (w < 0)
					die("%s: write: %s", path, strerror(errno));
			}
			pos += n;
			done += n;
		}
	}
	if (ftruncate(out, st->st_size))
		die("%s: %s", path, strerror(errno));
	if (close(out))
		die("%s: write: %s", path, strerror(errno));
	close(in);
	return done;
}

/* names of one file: the first one copied of each inode with several names (a hash table: a tree of backups has
 * millions of them) */
struct seen { dev_t dev; ino_t ino; char *path; };
static struct seen *seen;
static size_t nseen, seen_cap;

static size_t seen_slot(const struct seen *tab, size_t cap, dev_t dev, ino_t ino)
{
	size_t i = ((size_t)ino * 2654435761u ^ (size_t)dev) & (cap - 1);

	while (tab[i].path && !(tab[i].dev == dev && tab[i].ino == ino))
		i = (i + 1) & (cap - 1);
	return i;
}

static const char *seen_find(const struct stat *st)
{
	return seen_cap ? seen[seen_slot(seen, seen_cap, st->st_dev, st->st_ino)].path : NULL;
}

static void seen_add(const struct stat *st, const char *path)
{
	size_t i;

	if (2 * (nseen + 1) > seen_cap) {
		size_t cap = seen_cap ? 2 * seen_cap : 1024;
		struct seen *tab = calloc(cap, sizeof(*tab));

		if (!tab)
			die("out of memory");
		for (i = 0; i < seen_cap; i++)
			if (seen[i].path)
				tab[seen_slot(tab, cap, seen[i].dev, seen[i].ino)] = seen[i];
		free(seen);
		seen = tab;
		seen_cap = cap;
	}
	i = seen_slot(seen, seen_cap, st->st_dev, st->st_ino);
	if (!seen[i].path) {
		seen[i] = (struct seen){ st->st_dev, st->st_ino, strdup(path) };
		if (!seen[i].path)
			die("out of memory");
		nseen++;
	}
}

/* the directory PATH's last part is in, opened below ROOTFD without following a link; the last part in *NAME.
 * MAKE: a directory that is missing is made (0700; LIKEFD's of that name gives it its owner, mode and times at the
 * end, see dirs[]). -1 with errno when a part is not a directory. */
struct dir { char *path; };
static struct dir *dirs;	/* directories to give their attributes to at the end, children first */
static size_t ndirs;

static void later(const char *path)
{
	dirs = realloc(dirs, (ndirs + 1) * sizeof(*dirs));
	if (!dirs)
		die("out of memory");
	dirs[ndirs++].path = strdup(path);
}

static int parent_of(int rootfd, char *path, const char **name, int make)
{
	int fd = dup(rootfd), next;
	char *p = path, *slash;

	if (fd < 0)
		die("%s", strerror(errno));
	while ((slash = strchr(p, '/'))) {
		*slash = 0;
		next = openat(fd, p, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
		if (next < 0 && errno == ENOENT && make) {
			if (mkdirat(fd, p, 0700) && errno != EEXIST)
				die("%s: %s", path, strerror(errno));
			later(path);
			next = openat(fd, p, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
		}
		*slash = '/';
		if (next < 0) {
			int e = errno;

			close(fd);
			errno = e;
			return -1;
		}
		close(fd);
		fd = next;
		p = slash + 1;
	}
	*name = p;
	return fd;
}

static int cmd_copy(const char *src, const char *dst, const char *map, int by_link)
{
	char *line = NULL;
	size_t cap = 0, i;
	unsigned long long entries = 0, bytes = 0, linked = 0, gone = 0;
	int sroot = open(src, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	int droot = open(dst, O_RDONLY | O_DIRECTORY | O_CLOEXEC);

	if (sroot < 0)
		die("%s: %s", src, strerror(errno));
	if (droot < 0)
		die("%s: %s", dst, strerror(errno));
	if (map)
		read_idmap(map);
	lax = geteuid() != 0;
	while (getline(&line, &cap, stdin) > 0) {
		char *path = unescape(line);
		const char *sname, *dname;
		struct stat st, dt;
		int sfd, dfd;

		sfd = parent_of(sroot, path, &sname, 0);
		if (sfd < 0 || fstatat(sfd, sname, &st, AT_SYMLINK_NOFOLLOW)) {
			if (errno != ENOENT && errno != ENOTDIR)
				die("%s: %s", path, strerror(errno));
			gone++;
			if (sfd >= 0)
				close(sfd);
			continue;
		}
		if (S_ISSOCK(st.st_mode)) {	/* a socket is made by the program that listens on it */
			close(sfd);
			continue;
		}
		dfd = parent_of(droot, path, &dname, 1);
		if (dfd < 0)
			die("%s: a part of it is not a directory in %s (a link is never written through)", path, dst);
		if (!fstatat(dfd, dname, &dt, AT_SYMLINK_NOFOLLOW)) {
			if (S_ISDIR(dt.st_mode) && !S_ISDIR(st.st_mode))
				die("%s: a directory is in the way in %s", path, dst);
			if (!S_ISDIR(dt.st_mode) && unlinkat(dfd, dname, 0))
				die("%s: %s", path, strerror(errno));
		}
		entries++;
		if (S_ISDIR(st.st_mode)) {
			if (mkdirat(dfd, dname, 0700) && errno != EEXIST)
				die("%s: %s", path, strerror(errno));
			later(path);
		} else if (S_ISLNK(st.st_mode)) {
			char *target = xmalloc(st.st_size + 2);
			ssize_t n = readlinkat(sfd, sname, target, st.st_size + 1);

			if (n < 0)
				die("%s: %s", path, strerror(errno));
			target[n] = 0;
			if (symlinkat(target, dfd, dname))
				die("%s: %s", path, strerror(errno));
			free(target);
			copy_meta(sfd, sname, dfd, dname, &st, path);
		} else if (S_ISREG(st.st_mode)) {
			int remap = map_id('u', st.st_uid) != st.st_uid || map_id('g', st.st_gid) != st.st_gid;

			const char *first = st.st_nlink > 1 ? seen_find(&st) : NULL;

			if (first) {
				/* another name of a file that is there already */
				if (linkat(droot, first, dfd, dname, 0))
					die("%s: %s", path, strerror(errno));
			} else {
				if (by_link && !remap && !linkat(sfd, sname, dfd, dname, 0)) {
					linked++;
				} else {
					unsigned long long n = copy_data(sfd, sname, dfd, dname, &st, path);

					if (n == GONE) {
						gone++;
						entries--;
						close(sfd);
						close(dfd);
						continue;
					}
					bytes += n;
					copy_meta(sfd, sname, dfd, dname, &st, path);
				}
				if (st.st_nlink > 1)
					seen_add(&st, path);
			}
		} else {
			if (mknodat(dfd, dname, st.st_mode, st.st_rdev)) {
				if (!lax)
					die("%s: %s", path, strerror(errno));
			} else {
				copy_meta(sfd, sname, dfd, dname, &st, path);
			}
		}
		close(sfd);
		close(dfd);
	}
	/* the directories last, the deepest first: what was put into them changed their times */
	for (i = ndirs; i-- > 0;) {
		char *path = dirs[i].path;
		const char *sname, *dname;
		struct stat st;
		int sfd = parent_of(sroot, path, &sname, 0), dfd;

		if (sfd < 0 || fstatat(sfd, sname, &st, AT_SYMLINK_NOFOLLOW) || !S_ISDIR(st.st_mode)) {
			if (sfd >= 0)
				close(sfd);
			continue;
		}
		dfd = parent_of(droot, path, &dname, 0);
		if (dfd < 0)
			die("%s: %s", path, strerror(errno));
		copy_meta(sfd, sname, dfd, dname, &st, path);
		close(sfd);
		close(dfd);
	}
	printf("%llu entries, %llu bytes copied, %llu files linked, %llu gone meanwhile\n", entries, bytes, linked, gone);
	return 0;
}

int main(int argc, char **argv)
{
	if (argc == 3 && !strcmp(argv[1], "scan"))
		return cmd_walk(argv[2], NULL, NULL);
	if (argc == 5 && !strcmp(argv[1], "manifest"))
		return cmd_walk(argv[2], argv[3], argv[4]);
	if (argc == 3 && !strcmp(argv[1], "sum"))
		return cmd_sum(argv[2]);
	if ((argc == 4 || argc == 5) && (!strcmp(argv[1], "copy") || !strcmp(argv[1], "link")))
		return cmd_copy(argv[2], argv[3], argc == 5 ? argv[4] : NULL, argv[1][0] == 'l');
	fprintf(stderr, "usage: mu300-carry scan ROOT | sum ROOT < PATHS | manifest ROOT IMAGE TAG |\n"
			"       mu300-carry copy|link SRC DST [IDMAP] < PATHS\n");
	return 2;
}
