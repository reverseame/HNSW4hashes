import os
import statistics
import sys
from subprocess import Popen, PIPE
import common.utilities as util

def run_search_insert_test(M: int=4, ef: int=4, Mmax: int=16,\
                            Mmax0: int=16, algorithm="", bf: float=0.0,\
                            search_recall: int=4, dump_filename: str=None, npages: int=200):
    cmd = ["python3", "-m", "tests.search_insert_times_test"]

    cmd.extend(["--M", str(M)]);
    cmd.extend(["--ef", str(ef)]);
    cmd.extend(["--Mmax", str(Mmax)]);
    cmd.extend(["--Mmax0",  str(Mmax0)]);
    cmd.extend(["-algorithm",  str(algorithm)]);
    if bf > 0:
        cmd.extend(["--beer-factor", str(bf)]);
    cmd.extend(["--search-recall", str(search_recall)]);
    if dump_filename:
        cmd.extend(["--dump-file", str(dump_filename)]);
    cmd.extend(["--npages", str(npages)]);

    process = Popen(cmd, stdout=PIPE, stderr=PIPE)
    stdout, stderr = process.communicate()
    return stdout, stderr

if __name__ == '__main__':
    parser  = util.configure_argparse()
    parser.add_argument('-recall', '--search-recall', type=int, default=4, help="Search recall (default=4)")
    parser.add_argument('-dump', '--dump-file', type=str, help="Filename to dump Apotheosis data structure")
    parser.add_argument('--npages', type=int, default=1000, help="Number of pages to test (default=1000)")
    parser.add_argument('--factor', type=int, default=10, help="Max values of M, Mmax, and Mmax0 (default=10)")
    args    = parser.parse_args()
    util.configure_logging(args.loglevel.upper())
   
    # stdout, stderr =run_search_insert_test(args.M, args.ef, args.Mmax, args.Mmax0,\
    #                        args.distance_algorithm, args.beer_factor,\
    #                        args.search_recall, args.dump_file, args.npages)
    #stdout_lines = [s.decode("utf-8") for s in stdout.splitlines()]
    # experiments for M
    f = open(f"log_{args.factor}_{args.search_recall}_{args.npages}.out", "w")

    M       = range(4, 4*(args.factor + 1), 4)
    Mmax    = range(4, 4*(args.factor + 1), 4)
    Mmax0   = range(4, 4*(args.factor + 1), 4)
    collisions = set()
    f.write(f'TYPE,M,MMAX,MMAX0,TIME\n')
    for m in M:
        for mmax in Mmax:
            insert_list = []
            search_list = []
            for mmax0 in Mmax0:
                stdout, stderr =run_search_insert_test(m, args.ef, mmax, mmax0,\
                            args.distance_algorithm, args.beer_factor,\
                            args.search_recall, args.dump_file, args.npages)
                # get search and insert times
                stdout_lines = [s.decode("utf-8") for s in stdout.splitlines()]
                for line in stdout_lines:
                    if "SEARCH" not in line:
                        if "INSERT" not in line:
                            continue
                        else:
                            insert_time = float(line.split(':')[1])
                            f.write(f'I,{m},{mmax},{mmax0},{insert_time}\n')
                            insert_list.append(insert_time)
                    else:
                        search_time = float(line.split(':')[1])
                        f.write(f'S,{m},{mmax},{mmax0},{search_time}\n')
                        search_list.append(search_time)
                # get collisions
                stderr_lines = [s.decode("utf-8") for s in stderr.splitlines()]
                for line in stderr_lines:
                    line  = line.split("\"")
                    _hash = line[1]
                    collisions.add(_hash)
    f.close()
    
    f = open(f"collisions_{args.factor}_{args.search_recall}_{args.npages}.out", "w")
    for collision in collisions:
        f.write(collision, "\n")
    f.close()
                        