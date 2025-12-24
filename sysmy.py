import struct
import sys
import os

SECTOR = 512
CLUSTER = 4096
MAGIC = b"MYSYS   "

def u32(b,o): return struct.unpack_from("<I", b, o)[0]
def w32(b,o,v): struct.pack_into("<I", b, o, v)

# -------------------------------------------------
# NTFSX CORE
# -------------------------------------------------

class NTFSX:
    def __init__(self, img):
        self.f = open(img, "r+b")
        self.load_super()
        self.load_bitmap()

    def load_super(self):
        self.f.seek(0)
        sb = self.f.read(SECTOR)
        if sb[3:11] != MAGIC:
            raise RuntimeError("Não é NTFSX")
        self.total_clusters = u32(sb, 8)
        self.bitmap_cluster = 1
        self.mft_cluster = 2
        self.root_cluster = 3
        self.data_start = 4

    def load_bitmap(self):
        self.f.seek(self.bitmap_cluster * CLUSTER)
        self.bitmap = bytearray(self.f.read(CLUSTER))

    def save_bitmap(self):
        self.f.seek(self.bitmap_cluster * CLUSTER)
        self.f.write(self.bitmap)

    # ---------------- clusters ----------------

    def alloc_cluster(self):
        for i in range(self.data_start, self.total_clusters):
            byte = i // 8
            bit  = i % 8
            if not (self.bitmap[byte] & (1 << bit)):
                self.bitmap[byte] |= (1 << bit)
                self.save_bitmap()
                return i
        raise RuntimeError("Disco cheio")

    def read_cluster(self, c):
        self.f.seek(c * CLUSTER)
        return self.f.read(CLUSTER)

    def write_cluster(self, c, data):
        self.f.seek(c * CLUSTER)
        self.f.write(data.ljust(CLUSTER, b'\x00'))

    # ---------------- MFT ----------------

    def read_mft(self):
        data = self.read_cluster(self.mft_cluster)
        records = {}
        for i in range(0, CLUSTER, 128):
            r = data[i:i+128]
            if r[0] == 0:
                continue
            name = r[0:32].split(b'\x00')[0].decode()
            typ  = r[32]
            size = u32(r,33)
            first = u32(r,37)
            parent = u32(r,41)
            records[i//128] = (name,typ,size,first,parent)
        return records

    def add_mft(self, name, typ, size, first, parent):
        data = bytearray(self.read_cluster(self.mft_cluster))
        for i in range(0, CLUSTER, 128):
            if data[i] == 0:
                data[i:i+32] = name.encode()[:32].ljust(32,b'\x00')
                data[i+32] = typ
                w32(data,i+33,size)
                w32(data,i+37,first)
                w32(data,i+41,parent)
                self.write_cluster(self.mft_cluster, data)
                return i//128
        raise RuntimeError("MFT cheia")

    # ---------------- directory ----------------

    def dir_entries(self, dir_cluster):
        data = self.read_cluster(dir_cluster)
        ids=[]
        for i in range(0, CLUSTER, 4):
            v = u32(data,i)
            if v!=0:
                ids.append(v)
        return ids

    def dir_add(self, dir_cluster, mft_id):
        data = bytearray(self.read_cluster(dir_cluster))
        for i in range(0, CLUSTER, 4):
            if u32(data,i)==0:
                w32(data,i,mft_id)
                self.write_cluster(dir_cluster,data)
                return
        raise RuntimeError("Diretório cheio")

    # ---------------- files ----------------

    def write_chain(self, data):
        first = prev = 0
        for i in range(0, len(data), CLUSTER-4):
            c = self.alloc_cluster()
            blk = bytearray(CLUSTER)
            if prev:
                w32(prev_blk,0,c)
                self.write_cluster(prev, prev_blk)
            if first == 0:
                first = c
            blk[4:] = data[i:i+CLUSTER-4]
            prev_blk = blk
            prev = c
        w32(prev_blk,0,0)
        self.write_cluster(prev, prev_blk)
        return first

    def read_chain(self, first):
        out = bytearray()
        c = first
        while c != 0:
            blk = self.read_cluster(c)
            nxt = u32(blk,0)
            out += blk[4:]
            c = nxt
        return out

# -------------------------------------------------
# SHELL
# -------------------------------------------------

def shell(img):
    fs = NTFSX(img)
    cwd_cluster = fs.root_cluster
    cwd_mft = 0
    path = "/"

    while True:
        cmd = input(f"{path}> ").strip().split()
        if not cmd:
            continue

        if cmd[0] == "exit":
            break

        mft = fs.read_mft()

        if cmd[0] == "dir":
            for mid in fs.dir_entries(cwd_cluster):
                n,t,s,f,p = mft[mid]
                print(f"{n:20} {'<DIR>' if t else s}")

        elif cmd[0] == "cd":
            if cmd[1]=="..":
                if cwd_mft!=0:
                    cwd_mft = mft[cwd_mft][4]
                    cwd_cluster = fs.root_cluster if cwd_mft==0 else mft[cwd_mft][3]
                    path = "/".join(path.rstrip("/").split("/")[:-1]) or "/"
            else:
                for mid in fs.dir_entries(cwd_cluster):
                    n,t,s,f,p = mft[mid]
                    if n==cmd[1] and t==1:
                        cwd_cluster=f
                        cwd_mft=mid
                        path += n + "/"
                        break

        elif cmd[0] == "mkdir":
            c = fs.alloc_cluster()
            fs.write_cluster(c, b'\x00')
            mid = fs.add_mft(cmd[1],1,0,c,cwd_mft)
            fs.dir_add(cwd_cluster,mid)

        elif cmd[0] == "copy":
            data = open(cmd[1],"rb").read()
            first = fs.write_chain(data)
            mid = fs.add_mft(os.path.basename(cmd[1]),0,len(data),first,cwd_mft)
            fs.dir_add(cwd_cluster,mid)

        elif cmd[0] == "type":
            for mid in fs.dir_entries(cwd_cluster):
                n,t,s,f,p = mft[mid]
                if n==cmd[1] and t==0:
                    d=fs.read_chain(f)
                    print(d[:s].decode(errors="ignore"))
                    break

        else:
            print("Comandos: dir cd mkdir copy type exit")

# -------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv)!=2:
        print("Uso: python ntfsx_shell_cd.py disco.img")
        sys.exit(1)
    shell(sys.argv[1])
