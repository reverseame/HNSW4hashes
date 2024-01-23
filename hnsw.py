import numpy as np
import random
import pickle
import time
import logging
import heapq
import os

# custom exceptions
from datalayer.errors import NodeNotFoundError
from datalayer.errors import NodeAlreadyExistsError
from datalayer.errors import HNSWUnmatchDistanceAlgorithmError
from datalayer.errors import HNSWUndefinedError
from datalayer.errors import HNSWIsEmptyError

__author__ = "Daniel Huici Meseguer and Ricardo J. Rodríguez"
__copyright__ = "Copyright 2024"
__credits__ = ["Daniel Huici Meseguer", "Ricardo J. Rodríguez"]
__license__ = "GPL"
__version__ = "0.2"
__maintainer__ = "Daniel Huici"
__email__ = "reverseame@unizar.es"
__status__ = "Development"

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logging.getLogger('pickle').setLevel(logging.WARNING)
logging.getLogger('numpy').setLevel(logging.WARNING)
logging.getLogger('time').setLevel(logging.WARNING)

class HNSW:
    # initial layer will have index 0
    
    def __init__(self, M, ef, Mmax, Mmax0,
                    distance_algorithm=None,
                    heuristic=False, extend_candidates=True, keep_pruned_conns=True):
        """Default constructor."""
        self._found_nearest_elements = []
        self._M = M
        self._Mmax = Mmax # max links per node
        self._Mmax0 = Mmax0 # max links per node at layer 0 
        self._ef = ef
        self._mL = 1.0 / np.log(self._M)
        self._enter_point = None
        self._nodes = dict()
        self._heuristic = heuristic
        self._extend_candidates = extend_candidates
        self._keep_pruned_conns = keep_pruned_conns
        self._distance_algorithm = distance_algorithm
    
    def _is_empty(self):
        """Returns True if the HNSW structure contains no node, False otherwise."""
        return (self._enter_point is None)
    
    def get_enter_point(self):
        """Getter for _enter_point."""
        return self._enter_point
    
    def get_distance_algorithm(self):
        """Getter for _distance_algorithm."""
        return self._distance_algorithm

    def _insert_node(self, node):
        """Inserts node in the HNSW structure.

        Arguments:
        node -- the new node to insert
        """
        _layer = node.get_layer()
        if self._nodes.get(_layer) is None:
            self._nodes[_layer] = list()
        
        self._nodes[_layer].append(node)

    def _descend_to_layer(self, query_node, layer=0):
        """Goes down to a specific layer and returns the enter point of that layer, 
        which is the nearest element to query_node.
        
        Arguments:
        query_node  -- the node to be inserted
        layer       -- the target layer (default 0)
        """
        enter_point = self._enter_point
        for layer in range(self._enter_point.get_layer(), layer, -1): # Descend to the given layer
            logging.debug(f"Visiting layer {layer}, ep: {enter_point}")
            current_nearest_elements = self._search_layer_knn(query_node, [enter_point], 1, layer)
            logging.debug(f"Current nearest elements: {current_nearest_elements}")
            if len(current_nearest_elements) > 0:
                if enter_point.get_id() != query_node.get_id():
                    # get the nearest element to query node if the enter_point is not the query node itself
                    enter_point = self._find_nearest_element(query_node, current_nearest_elements)
            else: #XXX is this path even feasible?
                logging.warning("No closest neighbor found at layer {}".format(layer))

        return enter_point

    def _same_distance_algorithm(self, node):
        """Checks if the distance algorithm associated to node matches with the distance algorithm
        associated to the HNSW structure and raises HNSWUnmatchDistanceAlgorithmError when they do not match
        
        Arguments:
        node    -- the node to check
        """
        if node.get_distance_algorithm() != self.get_distance_algorithm():
            raise HNSWUnmatchDistanceAlgorithmError

    def add_node(self, new_node):
        """Adds a new node to the HNSW structure. On success, it return True
        Raises HNSWUnmatchDistanceAlgorithmError if the distance algorithm of the new node is distinct than 
        the distance algorithm associated to the HNSW structure.
        Raises NodeAlreadyExistsError if the HNSW already contains a node with the same ID as the new node.
        
        Arguments:
        new_node    -- the node to be added
        """
        # check if the HNSW distance algorithm is the same as the one associated to the node
        self._same_distance_algorithm(new_node)
        
        enter_point = self._enter_point
        # Calculate the layer to which the new node belongs
        new_node_layer = int(-np.log(random.uniform(0,1)) * self._mL) // 1 # l in MY-TPAMI-20
        new_node.set_max_layer(new_node_layer)
        logging.info(f"New node to insert: \"{new_node.get_id()}\" (assigned level: {new_node_layer})")
        
        if enter_point is not None:
            # checks if the enter point matches the new node and raises exception
            if enter_point.get_id() == new_node.get_id():
                raise NodeAlreadyExistsError
            
            Lep = enter_point.get_layer()
           
            logging.debug(f"Descending to layer {new_node_layer}")
            # Descend from the entry point to the layer of the new node...
            enter_point = self._descend_to_layer(new_node, layer=new_node_layer)

            logging.debug(f"Inserting \"{new_node.get_id()}\" using \"{enter_point}\" as enter point ...")
            # Insert the new node
            self._insert_node_to_layers(new_node, [enter_point])

            # Update enter_point of the HNSW, if necessary
            if new_node_layer > Lep:
                self._enter_point = new_node
                logging.info(f"Setting \"{new_node.get_id()}\" as enter point ... ")

        else:
            self._enter_point = new_node
            logging.info(f"Updating \"{new_node.get_id()}\" as enter point ... ")
        
        # store it now in its corresponding level
        self._insert_node(new_node)
        return True

    def _delete_neighbors_connections(self, node):
        """
        Given a node, delete its neighbors connectons for this node.
        """

        for layer in range(node.get_layer() + 1):
            for neighbor in node.get_neighbors_at_layer(layer):
                logging.info(f"Deleting at layer {layer} link with {neighbor}")
                neighbor.remove_neighbor(layer, node)


    def delete_node(self, node):
        """Deletes a node of the HNSW structure. On success, it returns True
        It may raise several exceptions:
            * HNSWIsEmptyError when the HNSW structure has no nodes.
            * NodeNotFoundError when the node to delete is not found in the HNSW structure.
            * HNSWUndefinedError when no neighbor is found at layer 0 (shall never happen this!).
            * HNSWUnmatchDistanceAlgorithmError when the distance algorithm of the node to delete is distinct than
              the distance algorithm associated to the HNSW structure.
        
        Arguments:
        node    -- the node to delete
        """
        # check if it is empty
        if self._is_empty():
            raise HNSWIsEmptyError
        # check if the HNSW distance algorithm is the same as the one associated to the node to delete
        self._same_distance_algorithm(node)
        
        # OK, you can try to search and delete the given node now
        # from the enter_point, reach the node, if exists
        enter_point = self._descend_to_layer(node)
        # now checks for the node, if it is in this layer
        found_node = self.search_layer_knn(node, [enter_point], 1, 0)
        if len(found_node) == 1:
            found_node = found_node.pop()
            if found_node.get_id() == node.get_id():
                logging.debug(f"Node {node} found! Deleting it ...")
                if found_node == self._enter_point: # cover the case we try to delete enter point
                    logging.info("Node is enter point! Searching for a new enter point first...")
                    for layer in range(self._enter_point.get_layer() + 1): # enter point may be alone, iterate layers below until we find a neighbor
                        closest_neighbor = self.select_neighbors_simple(found_node, found_node.get_neighbors_at_layer(layer), 1)
                        if len(closest_neighbor) == 1: # select new enter point: his closest neighbor
                            self._enter_point = closest_neighbor.pop()
                            break

                # now safely delete neighbor's connections
                self._delete_neighbors_connections(found_node)
            else:
                raise NodeNotFoundError
        else:
            # It should always get one closest neighbor, unless it is empty
            raise HNSWIsEmptyError
        return True

    def _already_exists(self, query_node, node_list) -> bool:
        """Returns True if query_node is contained in node_list, False otherwise.

        Arguments:
        query_node  -- the node to search
        node_list   -- the list of nodes where to search
        """
        for node in node_list:
            if node.get_id() == query_node.get_id():
                return True
        return False
    
    def _shrink_nodes(self, nodes, layer):
        """Shrinks the maximum number of neighbors of nodes in a given layer.
        The maximum value depends on the layer (MMax0 for layer 0 or Mmax for other layers).

        Arguments:
        nodes   -- list of nodes to shrink
        layer   -- current layer to search neighbors and update in each node 
        """

        mmax = self._Mmax0 if layer == 0 else self._Mmax

        for _node in nodes:
            _list = _node.get_neighbors_at_layer(layer)
            if (len(_list) > mmax):
                _node.set_neighbors_at_layer(layer, self._select_neighbors(node, _list, mmax, layer))
                logging.debug(f"Node {_node.id} exceeded Mmax. New neighbors: {[n.id for n in node.get_neighbors_at_layer(layer)]}")

    def _insert_node_to_layers(self, new_node, enter_point):
        """Inserts the new node from the minimum layer between HNSW enter point and the new node until layer 0.
        The first visited layer uses enter point as initial point for the best place to insert.
        It raises NodeAlreadyExistsError if the node already exists in the HNSW structure.

        Arguments:
        new_node    -- the node to insert
        enter_point -- the enter point to the first layer to visit 
        """
        
        min_layer = min(self._enter_point.get_layer(), new_node.get_layer())
        for layer in range(min_layer, -1, -1):
            currently_found_nn = self._search_layer_knn(new_node, enter_point, self._ef, layer)
            new_neighbors = self._select_neighbors(new_node, currently_found_nn, self._M, layer)
            logging.debug(f"Found nn at L{layer}: {currently_found_nn}")

            if self._already_exists(new_node, currently_found_nn) or \
                    self._already_exists(new_node, new_neighbors): 
                if new_node.get_layer() > layer: # in case we have already added links on layers above
                    for l in range(0, new_node.get_layer() + 1): # also delete neighbor its neighbor links above
                        for neighbor in new_node.get_neighbors_at_layer(l):
                            neighbor.remove_neighbor(l, new_node)
                raise NodeAlreadyExistsError

            # connect both nodes bidirectionally
            for neighbor in new_neighbors: 
                neighbor.add_neighbor(layer, new_node)
                new_node.add_neighbor(layer, neighbor)
                logging.info(f"Connections added at L{layer} between {new_node} and {neighbor}")
            
            # shrink (when we have exceeded the Mmax limit)
            self._shrink_nodes(new_neighbors, layer)
            enter_point.extend(currently_found_nn)
        
    def _search_layer_knn(self, query_node, enter_points, ef, layer):
        """Performs a k-NN search in a specific layer of the graph.

        Arguments:
        query_node      -- the node to search
        enter_points    -- current enter points
        ef              -- number of nearest elements to query_node to return
        layer           -- layer number
        """
        visited_elements = set(enter_points) # v in MY-TPAMI-20
        candidates = [] # C in MY-TPAMI-20
        currently_found_nearest_neighbors = set(enter_points) # W in MY-TPAMI-20

        # set variable for heapsort ordering, it depends on the direction of the trend score
        if not self._distance_algorithm.is_spatial():
            queue_multiplier = 1 # similarity metric
        else:
            queue_multiplier = -1 # distance metric

        # and initialize the priority queue with the existing candidates (from enter_points)
        for candidate in set(enter_points):
            distance = candidate.calculate_similarity(query_node)
            heapq.heappush(candidates, (distance*queue_multiplier, candidate))

        logging.info(f"Performing a k-NN search in layer {layer} ...")
        logging.debug(f"Candidates list: {candidates}")

        while len(candidates) > 0:
            logging.debug(f"Current NN found: {currently_found_nearest_neighbors}")
            # get the closest and furthest nodes from our candidates list
            furthest_node = self._find_furthest_element(query_node, currently_found_nearest_neighbors)
            logging.debug(f"Furthest node: {furthest_node}")
            _, closest_node = heapq.heappop(candidates)
            logging.debug(f" Closest node: {closest_node}")

            # closest node @candidates list is closer than furthest node @currently_found_nearest_neighbors            
            n2_is_closer_n1, _, _ = query_node.n2_closer_than_n1(n1=closest_node, n2=furthest_node)
            if n2_is_closer_n1:
                logging.debug("All elements in current nearest neighbors evaluated, exiting loop ...")
                break
            
            # get neighbor list in this layer
            _neighbor_list = closest_node.get_neighbors_at_layer(layer)
            logging.debug(f"Neighbour list of closest node: {_neighbor_list}")

            for neighbor in _neighbor_list:
                if neighbor not in visited_elements:
                    visited_elements.add(neighbor)
                    furthest_node = self._find_furthest_element(query_node, currently_found_nearest_neighbors)
                    
                    logging.debug(f"Neighbor: {neighbor}; furthest node: {furthest_node}")
                    # If the distance is smaller than the furthest node we have in our list, replace it in our list
                    n2_is_closer_n1, _, distance = query_node.n2_closer_than_n1(n2=neighbor, n1=furthest_node)
                    if n2_is_closer_n1 or len(currently_found_nearest_neighbors) < ef:
                        heapq.heappush(candidates, (distance*queue_multiplier, neighbor))
                        currently_found_nearest_neighbors.add(neighbor)
                        if len(currently_found_nearest_neighbors) > ef:
                            currently_found_nearest_neighbors.remove(self._find_furthest_element(query_node, currently_found_nearest_neighbors))
        logging.info(f"Current nearest neighbors at L{layer}: {currently_found_nearest_neighbors}")
        return currently_found_nearest_neighbors

    #TODO Check algorithm
    def search_layer_percentage(self, node_query, enter_points, percentage):
        visited_elements = set(enter_points)
        candidates = []
        currently_found_nearest_neighbors = set(enter_points)
        final_elements = set()

        # Initialize the priority queue with the existing candidates
        for candidate in enter_points:
            distance = candidate.calculate_similarity(node_query)
            heapq.heappush(candidates, (distance*self.queue_multiplier, candidate))

        furthest_node = self.find_furthest_element(node_query, currently_found_nearest_neighbors)
        while len(candidates) > 0:
            # Get the closest node from our candidates list
            _, closest_node = heapq.heappop(candidates)

            # Check if the closest node from the candidates list is closer than the furthest node from the list            
            if node_query.who_is_closer(closest_node, furthest_node): 
                break # All elements from currently_found_nearest_neighbors have been evaluated

            # Add new candidates to the priority queue
            for neighbor in closest_node.neighbors[0]:
                if neighbor not in visited_elements:
                    visited_elements.add(neighbor)
                    distance = neighbor.calculate_similarity(node_query)
                    # If the neighbor's distance satisfies the threshold, it joins the list.
                    if (node_query.who_is_closer(neighbor, furthest_node)):
                        heapq.heappush(candidates, (distance*self.queue_multiplier, neighbor))
                        if (distance > percentage):
                            final_elements.add(neighbor)

        return final_elements

    def _select_neighbors_heuristics(self, node, candidates: set, M, 
                                    layer, extend_candidates, keep_pruned_conns):
        """Returns the M nearest neighbors to node from the list of candidates.
        This corresponds to Algorithm 4 in MY-TPAMI-20.

        Arguments:
        node                -- base element
        candidates          -- candidate set
        M                   -- number of neighbors to return
        layer               -- layer number
        extend_candidates   -- flag to indicate whether or not to extend candidate list
        keep_pruned_conns   -- flag to indicate whether or not to add discarded elements
        """

        logging.info(f"Selecting neighbors with a heuristic search in layer {layer} ...")
        
        _r = set()
        _working_candidates = candidates
        if extend_candidates:
            logging.debug(f"Initial candidate set: {candidates}")
            logging.debug("Extending candidates ...")
            for candidate in candidates:
                _neighborhood_e = candidate.get_neighbors_at_layer(layer)
                for _neighbor in _neighborhood_e:
                    _working_candidates.add(_neighbor)

        logging.debug(f"Candidates list: {candidates}")
        
        _discarded = set()
        while len(_working_candidates) > 0 and len(_r) < M:
            # get nearest from W and from R and compare which is closer to new_node
            _elm_nearest_W  = self._find_nearest_element(node, _working_candidates)
            _working_candidates.remove(_elm_nearest_W)
            if len(_r) == 0: # trick for first iteration
                _r.add(_elm_nearest_W)
                logging.debug(f"Adding {_elm_nearest_W} to R")
                continue

            _elm_nearest_R  = self._find_nearest_element(node, _r)
            logging.debug(f"Nearest_R vs nearest_W: {_elm_nearest_R} vs {_elm_nearest_W}")
            n2_is_closer_n1, _, _ = node.n2_closer_than_n1(n1=_elm_nearest_R, n2=_elm_nearest_W)
            if n2_is_closer_n1:
                _r.add(_elm_nearest_W)
                logging.debug(f"Adding {_elm_nearest_W} to R")
            else:
                _discarded.add(_elm_nearest_W)
                logging.debug(f"Adding {_elm_nearest_W} to discarded set")

        if keep_pruned_conns:
            logging.debug("Keeping pruned connections ...")
            while len(_discarded) > 0 and len(_r) < M:
                _elm = self._find_nearest_element(node, _discarded)
                _discarded.remove(_elm)
                
                _r.add(_elm)
                logging.debug(f"Adding {_elm} to R")

        logging.debug(f"Neighbors: {_r}")
        return _r

    def _select_neighbors_simple(self, node, candidates: set, M):
        """Returns the M nearest neighbors to node from the list of candidates.
        This corresponds to Algorithm 3 in MY-TPAMI-20.

        Arguments:
        node        -- base element
        candidates  -- candidate set
        M           -- number of neighbors to return
        """
        nearest_neighbors = sorted(candidates, key=lambda obj: obj.calculate_similarity(node))
        logging.info(f"Neighbors to <{node}>: {nearest_neighbors}")
        if not self._distance_algorithm.is_spatial(): # similarity metric
            return nearest_neighbors[-M:] 
        else: # distance metric
            return nearest_neighbors[:M] 
    
    def _select_neighbors(self, node, candidates, M, layer): # heuristic params
        """Returns the M nearest neighbors to node from the set of candidates.
        If not _heuristic, it uses a simple selection of neighbors (Algorithm 3 in MY-TPAMI-20).
        Otherwise, it uses a heuristic selection (Algorithm 4 in MY-TPAMI-20)

        Arguments:
        node        -- base element
        candidates  -- candidate set
        M           -- number of neighbors to return
        layer       -- layer number
        """
        if not self._heuristic:
            return self._select_neighbors_simple(node, candidates, M)
        else:
            return self._select_neighbors_heuristics(node, candidates, M,
                                                layer,
                                                self._extend_candidates, self._keep_pruned_conns)
    
    def _find_furthest_element(self, node, nodes):
        """Returns the furthest element from nodes to node.

        Arguments:
        node    -- the base node
        nodes   -- the list of candidate nodes 
        """
        if not self._distance_algorithm.is_spatial(): # similarity metric
            return min((n for n in nodes), key=lambda n: node.calculate_similarity(n), default=None)
        else: # distance metric
            return max((n for n in nodes), key=lambda n: node.calculate_similarity(n), default=None)

    def _find_nearest_element(self, node, nodes):
        """Returns the nearest element from nodes to node.

        Arguments:
        node    -- the base node
        nodes   -- the list of candidate nodes 
        """
        if not self._distance_algorithm.is_spatial(): # similarity metric
            return max((n for n in nodes), key=lambda n: node.calculate_similarity(n), default=None)
        else: # distance metric
            return min((n for n in nodes), key=lambda n: node.calculate_similarity(n), default=None)

    def dump(self, file):
        """Saves HNSW structure to permanent storage.

        Arguments:
        file    -- filename to save 
        """

        with open(file, "wb") as f:
            pickle.dump(self, f, protocol=pickle.DEFAULT_PROTOCOL)

    @classmethod
    def load(cls, file):
        """Restores HNSW structure from permanent storage.
        
        Arguments:
        file    -- filename to load
        """
        with open(file, "rb") as f:
            obj = pickle.load(f)
        if not isinstance(obj, cls):
            raise TypeError(f"Expected an instance of {cls.__name__}, but got {type(obj).__name__}")
        return obj

    def knn_search(self, query, k, ef=0): 
        """Performs k-nearest neighbors search using the HNSW structure.
        It returns a dictionary (keys are similarity score) of k nearest neighbors (the values inside the dict) to the query node.
        Raises HNSWUnmatchDistanceAlgorithmError if the distance algorithm of the new node is distinct than 
        the distance algorithm associated to the HNSW structure.
        
        Arguments:
        query   -- the node for which to find the k nearest neighbors
        k       -- the number of nearest neighbors to retrieve
        ef      -- the exploration factor (controls the search recall)
        """
        
        # check if the HNSW distance algorithm is the same as the one associated to the query node
        self._same_distance_algorithm(query)

        # update ef to efConstruction, if necessary
        if ef == 0: 
            ef = self._ef

        enter_point = self._descend_to_layer(query, layer=1) 
            
        # and now get the nearest elements
        current_nearest_elements = self._search_layer_knn(query, [enter_point], ef, 0)
        _knn_list = self._select_neighbors(query, current_nearest_elements, k, 0)
        # return a dictionary of nodes and similarity score
        _knn_list = sorted(_knn_list, key=lambda obj: obj.calculate_similarity(query))
        _result = {}
        for _node in _knn_list:
            _value = _node.calculate_similarity(query)
            if _result.get(_value) is None:
                _result[_value] = []
            _result[_value].append(_node)

        return _result

    #TODO Check algorithm
    def percentage_search(self, query, percentage):
        """
            Performs a percentage search tºo retrieve nodes that satisfy a certain similarity threshold using the HNSW algorithm.
        
        Args:
            query: The query node for which to find the nearest neighbors.
            percentage: The threshold percentage for similarity. Nodes with similarity greater than or less than to this
                    threshold will be returned.
        
        Returns:
            A list of nearest neighbor nodes that satisfy the specified similarity threshold.

        """

        current_nearest_elements = []
        enter_point = [self.enter_point]
        for layer in range(self.enter_point.layer, 0, -1): # Descend to layer 1
            current_nearest_elements = self.search_layer_knn(query, enter_point, 1, layer)
            enter_point = [self._find_nearest_element(query, current_nearest_elements)]
        
        return self.search_layer_percentage(query, enter_point, percentage)
    
# unit test
import argparse
from datalayer.node.node_hash import HashNode
from datalayer.hash_algorithm.tlsh_algorithm import TLSHHashAlgorithm
if __name__ == "__main__":
    # get log level from command line
    parser = argparse.ArgumentParser()
    parser.add_argument('--M', type=int, default=4, help="Number of established connections of each node (default=4)")
    parser.add_argument('--ef', type=int, default=4, help="Exploration factor (determines the search recall, default=4)")
    parser.add_argument('--Mmax', type=int, default=8, help="Max links allowed per node at any layer, but layer 0 (default=8)")
    parser.add_argument('--Mmax0', type=int, default=16, help="Max links allowed per node at layer 0 (default=16)")
    parser.add_argument('--heuristic', help="Create a HNSW structure using a heuristic to select neighbors rather than a simple selection algorithm (disabled by default)", action='store_true')
    parser.add_argument('--no-extend-candidates', help="Neighbor heuristic selection extendCandidates parameter (enabled by default)", action='store_true')
    parser.add_argument('--no-keep-pruned-conns', help="Neighbor heuristic selection keepPrunedConns parameter (enabled by default)", action='store_true')
    parser.add_argument('-log', '--loglevel', choices=["debug", "info", "warning", "error", "critical"], default='warning', help="Provide logging level (default=warning)")

    args = parser.parse_args()
    breakpoint()
    # Create an HNSW structure
    logging.basicConfig(format='%(levelname)s:%(message)s', level=args.loglevel.upper())
    myHNSW = HNSW(M=args.M, ef=args.ef, Mmax=args.Mmax, Mmax0=args.Mmax0,\
                    heuristic=args.heuristic, extend_candidates=not args.no_extend_candidates, keep_pruned_conns=not args.no_keep_pruned_conns,\
                    distance_algorithm=TLSHHashAlgorithm)

    # Create the nodes based on TLSH Fuzzy Hashes
    node1 = HashNode("T12B81E2134758C0E3CA097B381202C62AC793B46686CD9E2E8F9190EC89C537B5E7AF4C", TLSHHashAlgorithm)
    node2 = HashNode("T10381E956C26225F2DAD9D5C2C5C1A337FAF3708A25012B8A1EACDAC00B37D557E0E714", TLSHHashAlgorithm)
    node3 = HashNode("T1DF8174A9C2A506F9C6FFC292D6816333FEF1B845C419121A0F91CF5359B5B21FA3A304", TLSHHashAlgorithm)
    node4 = HashNode("T1DF8174A9C2A506F9C6FFC292D6816333FEF1B845C419121A0F91CF5359B5B21FA3A304", TLSHHashAlgorithm)
    node5 = HashNode("T1DF8174A9C2A506F9C6FFC292D6816333FEF1B845C419121A0F91CF5359B5B21FA3A305", TLSHHashAlgorithm)
    nodes = [node1, node2, node3]

    # Insert nodes on the HNSW structure
    if myHNSW.add_node(node1):
        print(f"Node \"{node1.get_id()}\" inserted correctly.")
    if myHNSW.add_node(node2):
        print(f"Node \"{node2.get_id()}\" inserted correctly.")
    if myHNSW.add_node(node3):
        print(f"Node \"{node3.get_id()}\" inserted correctly.")
    try:
        myHNSW.add_node(node4)
        print(f"WRONG --> Node \"{node4.get_id()}\" inserted correctly.")
    except NodeAlreadyExistsError:
        print(f"Node \"{node4.get_id()}\" cannot be inserted, already exists!")

    #breakpoint()
    print(f"Enter point: {myHNSW.get_enter_point()}")

    try:
        myHNSW.delete_node(node5)
    except NodeNotFoundError:
        print(f"Node \"{node5.get_id()}\" not found!")
    
    #myHNSW.delete_node(node3)

    # Perform k-nearest neighbor search based on TLSH fuzzy hash similarity
    query_node = HashNode("T1BF81A292E336D1F68224D4A4C751A2B3BB353CA9C2103BA69FA4C7908761B50F22E301", TLSHHashAlgorithm)
    for node in nodes:
        print(node, "Similarity score: ", node.calculate_similarity(query_node))

    print('Testing knn-search ...')
    results = myHNSW.knn_search(query_node, k=2, ef=4)
    print("Total neighbors found: ", len(results))
    # iterate now in the results. If we sort the keys, we can get them ordered by similarity score
    keys = sorted(results.keys())
    idx = 1
    for key in keys:
        for node in results[key]:
            print(f"Node ID {idx}: \"{node.get_id()}\"")
            idx += 1

    # Perform percentage search to retrieve nodes above a similarity threshold
    #results = myHNSW.percentage_search(query_node, percentage=60)
    #print(results)

    # Dump created HNSW structure to disk
    myHNSW.dump("myHNSW.txt")

    # Restore HNSW structure from disk
    myHNSW = HNSW.load("myHNSW.txt")
