# -*- coding: utf-8 -*-
import zlib
import logging
logger = logging.getLogger(__name__)

__author__ = "Daniel Huici Meseguer and Ricardo J. Rodríguez"
__copyright__ = "Copyright 2024"
__credits__ = ["Daniel Huici Meseguer", "Ricardo J. Rodríguez"]
__license__ = "GPL"
__version__ = "0.3"
__maintainer__ = "Daniel Huici"
__email__ = "reverseame@unizar.es"
__status__ = "Development"

# for compressed dumping
import gzip as gz
import io

from common.constants import * 

from datalayer.db_manager import DBManager

from datalayer.radix_hash import RadixHash
from datalayer.hnsw import HNSW
from datalayer.hash_algorithm.hash_algorithm import HashAlgorithm

# custom exceptions
from common.errors import NodeNotFoundError
from common.errors import NodeAlreadyExistsError

from common.errors import ApotheosisUnmatchDistanceAlgorithmError
from common.errors import ApotheosisIsEmptyError
from common.errors import ApotFileFormatUnsupportedError
from common.errors import ApotFileReadError

# preferred file extension
PREFERRED_FILEEXT = ".apo"

from apotheosis import Apotheosis

class ApotheosisWinModule(Apotheosis):
    
    def __init__(self, M=0, ef=0, Mmax=0, Mmax0=0,\
                    distance_algorithm=None,\
                    heuristic=False, extend_candidates=True, keep_pruned_conns=True,\
                    beer_factor: float=0,\
                    filename=None):
        """Default constructor."""
        if filename == None:
            self.create_empty(M=M, ef=ef, Mmax=Mmax, Mmax0=Mmax0, distance_algorithm=distance_algorithm,\
                                heuristic=heuristic, extend_candidates=extend_candidates, keep_pruned_conns=keep_pruned_conns,\
                                beer_factor=beer_factor)
        else:
            db_manager = DBManager() 
            # open the file and load structures from filename
            with open(filename, "rb") as f:
                # check if file is compressed and do stuff, if necessary
                f = Apotheosis._check_compression(f)
                # read the header and process
                data = f.read(HEADER_SIZE)
                # check header (file format and version match)
                rCRC32_bcfg, rCRC32_bep, rCRC32_bnodes = Apotheosis._assert_header(data)
                logger.debug(f"CRCs read: bcfg={hex(rCRC32_bcfg)}, bep={hex(rCRC32_bep)}, bnodes={hex(rCRC32_bnodes)}")
                # check HNSW cfg and load it if no error
                data = f.read(CFG_SIZE)
                CRC32_bcfg = zlib.crc32(data) & 0xffffffff
                if CRC32_bcfg != rCRC32_bcfg:
                    raise ApotFileReadError(f"CRC32 {hex(CRC32_bcfg)} of HNSW configuration does not match (should be {hex(rCRC32_bcfg)})")
                self._HNSW = HNSW.load_cfg_from_bytes(data)
               
                if self._HNSW.get_distance_algorithm() != distance_algorithm:
                    raise ApotheosisUnmatchDistanceAlgorithmError

                self._distance_algorithm = self._HNSW.get_distance_algorithm()
                pageid_to_node = {}
                pageid_neighs = {}
                logger.debug(f"Reading enter point from file \"{filename}\" ...")
                # now, process enter point
                ep, pageid_to_node, pageid_neighs, CRC32_bep, _ = \
                        ApotheosisWinModule._load_node_from_fp(f, pageid_to_node, with_layer=True,
                                                        algorithm=distance_algorithm, db_manager=db_manager)
                if CRC32_bep != rCRC32_bep:
                    raise ApotFileReadError(f"CRC32 {hex(CRC32_bep)} of enter point does not match (should be {hex(rCRC32_bep)})")
               
                self._HNSW._enter_point  = ep 
                self._HNSW._insert_node(ep) # internal, add the node to nodes dict
                # finally, process each node in each layer
                n_layers = f.read(I_SIZE)
                bnodes = n_layers
                n_layers = int.from_bytes(n_layers, byteorder=BYTE_ORDER)
                logger.debug(f"Reading {n_layers} layers ...")
                for _layer in range(0, n_layers):
                    # read the layer number
                    layer = f.read(I_SIZE)
                    bnodes += layer
                    layer = int.from_bytes(layer, byteorder=BYTE_ORDER)
                    # read the number of nodes in this layer
                    neighs_to_read = f.read(I_SIZE)
                    bnodes += neighs_to_read
                    neighs_to_read = int.from_bytes(neighs_to_read, byteorder=BYTE_ORDER)
                    logger.debug(f"Reading {neighs_to_read} nodes at L{layer} ...")
                    for idx in range(0, neighs_to_read):
                        new_node, pageid_to_node, current_pageid_neighs, _, bnode = \
                            ApotheosisWinModule._load_node_from_fp(f, pageid_to_node,  
                                                        algorithm=distance_algorithm, db_manager=db_manager)
                        new_node.set_max_layer(layer)
                        self._HNSW._insert_node(new_node) # internal, add the node to nodes dict
                        pageid_neighs.update(current_pageid_neighs)
                        bnodes += bnode
                    
                CRC32_bnodes = zlib.crc32(bnodes) & 0xffffffff
                logger.debug(f"Nodes loaded correctly. CRC32 computed: {hex(CRC32_bnodes)}")
                if CRC32_bnodes != rCRC32_bnodes:
                    raise ApotFileReadError(f"CRC32 {hex(CRC32_bnodes)} of nodes does not match (should be {hex(rCRC32_bnodes)})")
            # all done here, except we need to link neighbors...
            for pageid in pageid_neighs:
                # search the node -- this search should always return something
                try:
                    node = pageid_to_node[pageid]
                except Exception as e:
                    raise ApotFileReadError(f"Node with pageid {pageid} not found. Is this code correct?")
                
                neighs = pageid_neighs[pageid]
                for layer in neighs:
                    logger.debug(f"Recreating nodes at L{layer} ...")
                    neighs_at_layer = neighs[layer]
                    for neigh in neighs_at_layer:
                        logger.debug(f"Recreating node with pageid {neigh} at L{layer} ...")
                        # search the node -- this search should always return something
                        try:
                            neigh_node = pageid_to_node[neigh]
                        except Exception as e:
                            raise ApotFileReadError(f"Node with pageid {neigh} not found. Is this code correct?")
                        # add the link between them
                        node.add_neighbor(layer, neigh_node)
                        # (the other link will be set later, when processing the neigh as node)
            
            # recreate radix tree from HNSW (we can do it also in the loop above)
            self._radix = RadixHash(self._distance_algorithm, self._HNSW)

    @classmethod
    def _load_node_from_fp(cls, f, pageid_to_node: dict,  
                                with_layer:bool=False, algorithm: HashAlgorithm=None, db_manager=None):
        """Loads a node from a file pointer f.
        It is necessary to have a db_manager to load an Apotheasis file from disk
        (we only keep page ids and their relationships, nothing else).

        Arguments:
        f               -- file pointer to read from
        pageid_to_node  -- dict to map page ids to WinModuleHashNode (necessary for rebuilding indexes)
        with_layer      -- bool flag to indicate if we need to read the layer for this node (default False)
        algorithm       -- associated distance algorithm
        db_manager      -- db_manager to handle connections to DB (optional)
        """
        logger.debug("Loading a new node from file pointer ...")
       
        page_id     = f.read(I_SIZE)
        bnode       = page_id
        max_layer   = '(no layer)' 
        if with_layer:
            max_layer   = f.read(I_SIZE)
            bnode      += max_layer
            max_layer   = int.from_bytes(max_layer, byteorder=BYTE_ORDER)
        
        logger.debug(f"Read page id: {page_id}, layer: {max_layer} ...")
        page_id     = int.from_bytes(page_id, byteorder=BYTE_ORDER)
        # read neighborhoods
        nhoods      = f.read(I_SIZE)
        logger.debug(f"Read neighborhoods: {nhoods} ...")
        bnode      += nhoods
        nhoods      = int.from_bytes(nhoods, byteorder=BYTE_ORDER)
        logger.debug(f"Node {page_id} with {nhoods} neighborhoods, starts processing ...")
        neighs_page_id = {} 
        layer = 0
        # process each neighborhood, per layer and neighbors in that layer
        for nhood in range(0, nhoods):
            logger.debug(f"Processing neighborhood {nhood} ...")
            layer   = f.read(I_SIZE)
            neighs  = f.read(I_SIZE)
            logger.debug(f"Read {neighs} neighbors and layer {layer} ...")
            bnode  += layer + neighs
            layer   = int.from_bytes(layer, byteorder=BYTE_ORDER)
            neighs  = int.from_bytes(neighs, byteorder=BYTE_ORDER)
            neighs_page_id[layer] = []
            # get now the neighs page id at this layer 
            for idx_neigh in range(0, neighs):
                neigh_page_id = f.read(I_SIZE)
                logger.debug(f"Read neigh page id: {neigh_page_id} ...")
                bnode        += neigh_page_id
                neighs_page_id[layer].append(int.from_bytes(neigh_page_id, byteorder=BYTE_ORDER))
            logger.debug(f"Processed {neighs} at L{layer} ({neighs_page_id})")

        CRC32_bnode = zlib.crc32(bnode) & 0xffffffff
        logger.debug(f"New node with {page_id} at L{layer} successfully read. Neighbors page ids: {neighs_page_id}. Computed CRC32: {hex(CRC32_bnode)}")

        # retrieve the specific page id information from database and get a WinModuleHashNode
        logger.debug("Recovering data now from DB, if necessary ...")
        new_node        = None
        pageid_neighs   = {} 
        if db_manager:
            if pageid_to_node.get(page_id) is None:
                new_node = db_manager.get_winmodule_data_by_pageid(page_id=page_id, algorithm=algorithm)
                if algorithm == TLSHHashAlgorithm:
                    new_node._id = new_node._page.hashTLSH
                elif algorithm == SSDEEPHashAlgorithm:
                    new_node._id = new_node._page.hashSSDEEP
                else:
                    raise ApotFileFormatUnsupportedError
                if with_layer:
                    new_node.set_max_layer(max_layer)
                # store it for next iterations
                pageid_to_node[page_id] = new_node
            else:
                #breakpoint()
                new_node = pageid_to_node[page_id]
            logger.debug(f"Initial data set to new node: \"{new_node.get_id()}\" at L{max_layer}")

            # get now the neighboors
            if pageid_neighs.get(page_id) is None:
                pageid_neighs[page_id] = {}
            for layer, neighs_list in neighs_page_id.items():
                if pageid_neighs[page_id].get(layer) is None:
                    pageid_neighs[page_id][layer] = set()
                pageid_neighs[page_id][layer].update(neighs_list)
        else:
            logger.debug("No db_manager was given, skipping data retrieval from DB ...")

        return new_node, pageid_to_node, pageid_neighs, CRC32_bnode, bnode 

    @classmethod
    def load(cls, filename:str=None, distance_algorithm=None):
        """Restores Apotheosis structure from permanent storage.
        
        Arguments:
        filename            -- filename to load
        distance_algorithm  -- distance algorithm to check in the file
        """
        logger.info(f"Restoring Apotheosis structure from disk (filename \"{filename}\", distance algorithm {distance_algorithm}\") ...")
        newAPO = ApotheosisWinModule(filename=filename, distance_algorithm=distance_algorithm)
        return newAPO

# unit test
import common.utilities as util
from datalayer.node.hash_node import HashNode
from datalayer.node.winmodule_hash_node import WinModuleHashNode
from datalayer.hash_algorithm.tlsh_algorithm import TLSHHashAlgorithm
from datalayer.hash_algorithm.ssdeep_algorithm import SSDEEPHashAlgorithm
from random import random
import math

def rand(apo: Apotheosis):
    upper_limit = myAPO.get_distance_algorithm().get_max_hash_alphalen()
    return _rand(upper_limit)

def _rand(upper_limit: int=1):
    lower_limit = 0
    return math.floor(random()*(upper_limit - lower_limit) + lower_limit)


def search_knns(apo, query_node):
    try:
        exact_found, node, results = apo.knn_search(query=query_node, k=2, ef=4)
        print(f"{query_node.get_id()} exact found? {exact_found}")
        print("Total neighbors found: ", len(results))
        util.print_results(results)
    except ApotheosisIsEmptyError:
        print("ERROR: performing a KNN search in an empty Apotheosis structure")

if __name__ == "__main__":
    parser = util.configure_argparse()
    args = parser.parse_args()
    util.configure_logging(args.loglevel.upper())

    # Create an Apotheosis structure
    myAPO = ApotheosisWinModule(M=args.M, ef=args.ef, Mmax=args.Mmax, Mmax0=args.Mmax0,\
                    heuristic=args.heuristic, extend_candidates=not args.no_extend_candidates, keep_pruned_conns=not args.no_keep_pruned_conns,\
                    beer_factor=args.beer_factor,
                    distance_algorithm=TLSHHashAlgorithm)

    # Create the nodes based on TLSH Fuzzy Hashes
    hash1 = "T1BF81A292E336D1F68224D4A4C751A2B3BB353CA9C2103BA69FA4C7908761B50F22E301" #fake
    hash2 = "T12B81E2134758C0E3CA097B381202C62AC793B46686CD9E2E8F9190EC89C537B5E7AF4C" 
    hash3 = "T10381E956C26225F2DAD9D5C2C5C1A337FAF3708A25012B8A1EACDAC00B37D557E0E714"
    hash4 = "T1DF8174A9C2A506F9C6FFC292D6816333FEF1B845C419121A0F91CF5359B5B21FA3A304" 
    hash5 = "T1DF8174A9C2A506F9C6FFC292D6816333FEF1B845C419121A0F91CF5359B5B21FA3A305" #fake
    hash6 = "T1DF8174A9C2A506FC122292D644816333FEF1B845C419121A0F91CF5359B5B21FA3A305" #fake
    hash7 = "T10381E956C26225F2DAD9D097B381202C62AC793B37082B8A1EACDAC00B37D557E0E714" #fake

    node1 = WinModuleHashNode(hash1, TLSHHashAlgorithm)
    node2 = WinModuleHashNode(hash2, TLSHHashAlgorithm)
    node3 = WinModuleHashNode(hash3, TLSHHashAlgorithm)
    node4 = WinModuleHashNode(hash4, TLSHHashAlgorithm)
    node5 = WinModuleHashNode(hash5, TLSHHashAlgorithm)
    nodes = [node1, node2, node3]

    print("Testing insert ...")
    # Insert nodes on the HNSW structure
    if myAPO.insert(node1):
        print(f"Node \"{node1.get_id()}\" inserted correctly.")
    if myAPO.insert(node2):
        print(f"Node \"{node2.get_id()}\" inserted correctly.")
    if myAPO.insert(node3):
        print(f"Node \"{node3.get_id()}\" inserted correctly.")
    try:
        myAPO.insert(node4)
        print(f"WRONG --> Node \"{node4.get_id()}\" inserted correctly.")
    except NodeAlreadyExistsError:
        print(f"Node \"{node4.get_id()}\" cannot be inserted, already exists!")

    print(f"Enter point: {myAPO.get_HNSW_enter_point()}")

    # draw it
    if args.draw:
        myAPO.draw("unit_test.pdf")

    try:
        myAPO.delete(node5)
    except NodeNotFoundError:
        print(f"Node \"{node5.get_id()}\" not found!")

    print("Testing delete ...")
    if myAPO.delete(node1):
        print(f"Node \"{node1.get_id()}\" deleted!")

    # Perform k-nearest neighbor search based on TLSH fuzzy hash similarity
    query_node = HashNode("T1BF81A292E336D1F68224D4A4C751A2B3BB353CA9C2103BA69FA4C7908761B50F22E301", TLSHHashAlgorithm)
    for node in nodes:
        print(node, "Similarity score: ", node.calculate_similarity(query_node))

    print('Testing knn_search ...')
   
    search_knns(myAPO, node1)
    search_knns(myAPO, node5)
    print('Testing threshold_search ...')
    # Perform threshold search to retrieve nodes above a similarity threshold
    try:
        exact_found, node, results = myAPO.threshold_search(query_node, threshold=220, n_hops=3)
        print(f"{query_node.get_id()} exact found? {exact_found}")
        util.print_results(results, show_keys=True)
    except ApotheosisIsEmptyError:
        print("ERROR: performing a KNN search in an empty Apotheosis structure")

    from datetime import datetime
    now = datetime.now()
    date_time = now.strftime("%H:%M:%S")
    # Dump created Apotheosis structure to disk
    print(f"Saving ApotheosisWinModule at {date_time} ...")
    myAPO.dump("myAPO"+date_time)
    myAPO.dump("myAPO_uncompressed"+date_time, compress=False)

    # XXX use specific test in "tests" folder to check the load method
    
    # cluster test
    in_cluster = 10 # random nodes in the cluster
    alphabet = []
    for i in range(0, 10): # '0'..'9'
        alphabet.append(str(i + ord('0')))
    
    for i in range(0, 6): # 'A'..'F'
        alphabet.append(str(i + ord('0')))


    _nodes = []
    for i in range(0, in_cluster*100):
        limit = 0
        while limit <= 2:
            limit = _rand(len(alphabet))

        if random() >= .5: # 50%
            _hash = hash1
        else:
            _hash = hash2
        
        _hash = _hash[0:limit - 1] + alphabet[_rand(len(alphabet))] + _hash[limit + 1:]
        node = HashNode(_hash, TLSHHashAlgorithm)
        try:
            myAPO.insert(node)
            _nodes.add(node)
        except:
            continue
