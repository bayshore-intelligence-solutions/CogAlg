from collections import deque, defaultdict
from functools import reduce
from itertools import groupby, starmap

import numpy as np
import numpy.ma as ma

from frame_blobs import (
    scan_P_,
    form_seg_,
    form_blob,
    terminate_segment,
    terminate_blob,
)
'''
    intra_blob() evaluates for recursive frame_blobs() and comp_P() within each blob.
    combined frame_blobs and intra_blob form a 2D version of 1st-level algorithm.
    to be added:

    inter_sub_blob() will compare sub_blobs of same range and derivation within higher-level blob, bottom-up ) top-down:
    inter_level() will compare between blob levels, where lower composition level is integrated by inter_sub_blob
    match between levels' edges may form composite blob, axis comp if sub_blobs within blob margin?
    inter_blob() will be 2nd level 2D alg: a prototype for recursive meta-level alg

    Each intra_comp() call from intra_blob() adds a layer of sub_blobs, new dert to derts and Layer to Layers, in each blob.
    intra_comp also sends feedback to fork[fia][fder] in root_blob, then to root_blob.root_blob, etc.
    Blob structure:

    Dert: G, A, M, Dx, Dy, L, Ly,  # current Layer, += dert: g, a, m, (dx, dy), A: iG angle, None if gDert, M: match = min
    # summed across segment_, not forks, sub_blobs

    sign, # current g | ga sign
    rng,  # comp range, in each Dert
    map,  # boolean map of blob to compute overlap
    box,  # boundary box: y0, yn, x0, xn; selective map, box in lower Layers

    segment_[  # references down blob formation tree, in vertical (horizontal) order
        seg_params,
        Py_ [(P_params, derts_)]  # vertical buffer of Ps per segment
        # derts [(g_dert, ga_dert)]: two layers per intra_blob, sum in blob.rng, i = derts[-1][fia]
        ],
    derts__,  # intra_comp inputs
    forks [ layers [ Dert, sub_blob_] ]  # each fork (deriv+ | range+ | angle) contains layers across its derivation tree 

        # all fork' layers are accumulated by feedback, with merged | nested forking on Layers[1:]?
        # Dert is for layer-parallel comp_blob, None if len(sub_blob_) < min, same for forks and layers?

    root_blob, # reference for feedback of all Derts params summed in sub_blobs
    hDerts     # higher-Dert params += higher-dert params (including I), for layer-parallel comp_blob, no forking
    '''

# -----------------------------------------------------------------------------
# Filters

ave = 20  # average g, reflects blob definition cost, higher for smaller positive blobs, no intra_comp for neg blobs
kwidth = 3  # kernel width
if kwidth != 2:  # ave is an opportunity cost per comp:
    ave *= (kwidth ** 2 - 1) / 2  # ave *= ncomp_per_kernel / 2 (base ave is for ncomp = 2 in 2x2)

ave_blob = 10000  # fixed cost of intra_comp per blob, accumulated in deeper layers
rave = 20  # fixed root_blob / blob cost ratio: add sub_blobs, Levels+=Level, derts+=dert
ave_n_sub_blobs = 10  # determines rave, adjusted per intra_comp
ave_intra_blob = 1000  # cost of default eval_sub_blob_ per intra_blob

''' These filters are accumulated for evaluated intra_comp:
    Ave += ave: cost per next-layer dert, fixed comp grain: pixel
    ave_blob *= rave: cost per next root blob, variable len sub_blob_
    represented per fork if tree reorder, else redefined at each access?
'''

# Declare comparison flags:
F_ANGLE = 0b01
F_DERIV = 0b10


# -----------------------------------------------------------------------------
# Functions

def intra_comp(i__, dert___, root_blob, rng, fork_type, Ave, Ave_blob):
    i__ = i__[root_blob.slices]  # i__, dert__ within root_blob' box:
    dert__ = dert___[-1][root_blob.slices]
    y0, yn, x0, xn = root_blob['box']

    P__ = form_P__(x0, i__, dert__, Ave)  # horizontal clustering
    seg_ = deque()  # buffer of running segments

    for y, P_ in enumerate(P__, start=y0):
        P_ = scan_P_(P_, seg_,
                     form_blob_func=merge_segment,  # merge' arguments:
                     root_blob=root_blob, dert___=dert___, rng=rng, fork_type=fork_type)
        seg_ = form_seg_(y, P_,
                         form_blob_func=merge_segment,  # merge' arguments:
                         root_blob=root_blob, dert___=dert___, rng=rng, fork_type=fork_type)

    # merge last-line segments into their blobs:
    while seg_: form_blob(seg_.popleft(), root_blob, rng)

    return Ave_blob * len(root_blob['Layers'][-1][fork_type]['sub_blob_']) / ave_n_sub_blobs


def form_P__(x0, i__, dert__, Ave):  # forms Ps across the whole dert array.

    if i__.ndim == 2:  # inputs are g_derts
        dy_slice = 1
        dx_slice = 2
    elif i__.ndim == 3:  # inputs are ga_derts
        dy_slice = slice(1, 3)
        dx_slice = slice(3, None)
    else:
        raise ValueError
    g__ = dert__[0, :, :]

    s_x_L__ = starmap(lambda y, g_:  # dert clustering by g sign:
                      (y,
                       [(sign, next(group)[0], len(list(group)) + 1)
                        for sign, group in groupby(enumerate(g_), lambda x: x[1] > Ave)
                        if sign is not ma.masked]
                       ),
                      enumerate(g__))

    P__ = [deque(dict(sign=s,  # P accumulation:
                      x0=x + x0,
                      I=i_[..., x: x + L].sum(axis=-1),
                      G=dert_[0, x: x + L].sum() - Ave * L,
                      Dy=dert_[dy_slice, x: x + L].sum(axis=-1),
                      Dx=dert_[dx_slice, x: x + L].sum(axis=-1),
                      L=L,
                      dert_=dert_[:, x: x + L].T,
                      )
                 for s, x, L in s_x_L_)
           for i_, dert_, (y, s_x_L_) in zip(i__,
                                             dert__.swapaxes(0, 1),
                                             s_x_L__)]
    return P__


def merge_segment(seg, root_blob, dert___, rng, fork_type):

    blob = terminate_segment(seg)

    if blob['open_segments'] == 0:
        terminate_blob(blob, seg,
                       # Additional parameters to update blob:
                       rng=rng,
                       dert___=dert___,
                       root_blob=root_blob,
                       hDerts=np.concatenate(
                           (
                               np.array(root_blob['Dert'].values()),
                               root_blob['hDert'],
                           ),
                           axis=0,
                       ),
                       fork_type=fork_type,
                       )
        feedback(blob)


def feedback(blob):  # accumulate Dert and sub_blob_ of recursively higher root_fork

    root_blob = blob['root_blob']
    if root_blob is None: # Stop recursion.
        return
    fork_type = blob['fork_type']

    len_root_fork_layers = len(root_blob['forks'][fork_type])
    len_sub_fork_layers = max(*map(len, blob['fork'].values()))

    while len_root_fork_layers <= len_sub_fork_layers:
        # root_fork_layers == max (sub_layers), can't be > root layers,
        # feedback appends root layers at len_root_fork == len_sub_fork?

        root_blob['forks'][fork_type] = np.stack((
            root_blob['forks'][fork_type],
            (0, 0, 0, 0, 0),
        # is this supposed to initialize new layer?
        ))

    # layers accumulation (layer = Dert, sub_blob_):
    G, Dy, Dx, L, Ly = blob['Dert'].values()

    root_blob['forks'][fork_type][0][0] += [G, Dy, Dx, L, Ly]
    root_blob['forks'][fork_type][0][1].append(blob)  # sub_blob_

    root_blob['forks'][fork_type][1:][0] += blob['forks'][fork_type][:][0]
    root_blob['forks'][fork_type][1:][1].append(blob['forks'][fork_type][:][1])

    feedback(root_blob)


def intra_blob(root_blob, rng, eval_fork_, Ave_blob, Ave):  # fia (flag ia) selects input a | g in higher dert

    # two-level intra_comp eval per root_blob.sub_blob, deep intra_blob fork eval per input blob to last intra_comp
    # local fork's blob is initialized in prior intra_comp's feedback(), no lower Layers yet

    for blob in root_blob.sub_blob_:  # sub_blobs are evaluated for comp_fork, add nested fork indices?
        if blob.Dert[0] > Ave_blob:  # noisy or directional G | Ga: > intra_comp cost: rel root blob + sub_blob_

            Ave_blob = intra_comp(blob, rng, 0, Ave_blob, Ave)  # fa=0, Ave_blob adjust by n_sub_blobs
            Ave_blob *= rave  # estimated cost of redundant representations per blob
            Ave += ave  # estimated cost per dert

            for sub_blob in blob.sub_blob_:  # sub_sub_blobs evaluated for root_dert angle calc & comp
                if sub_blob.Dert[0] > Ave_blob:  # G > intra_comp cost;  no independent angle value

                    Ave_blob = intra_comp(sub_blob, rng, 1, Ave_blob, Ave)  # fa=1: same as fia?
                    Ave_blob *= rave  # Ave_blob adjusted by n_sub_blobs
                    Ave += ave
                    rdn = 1
                    G = sub_blob.high_Derts[-2][0]  # input gradient
                    Gg, Mg = sub_blob.high_Derts[-1][0, 2]  # from first intra_comp in current-intra_blob
                    Ga = sub_blob.Dert[0]  # from last intra_comp, no m_angle ~ no m_brightness: mag != value

                    eval_fork_ += [  # sort per append?
                        (G + Mg, 1),  # est. match of input gradient at rng+1, 0 if i is p, single exposed input per fork
                        (Gg, rng + 1),  # est. match of gg at rng+rng+1, initial fork, then more coarse?
                        (Ga, rng + 1)  # est. match of ga at rng+rng+1;  a_rng+/ g_rng+, no indep value, replaced by ga
                    ]
                    new_eval_fork_ = []  # forks recycled for next intra_blob
                    for val, irng in sorted(eval_fork_, key=lambda val: val[0], reverse=True):

                        if val > ave_intra_blob * rdn:  # cost of default eval_sub_blob_ per intra_blob
                            rdn += 1  # fork rdn = fork index + 1
                            rng += irng  # incremented by input rng, for current and recycled forks, or in next intra_blob?
                            Ave_blob += ave_blob * rave * rdn
                            Ave += ave * rdn
                            new_eval_fork_ += [(val, rng)]
                            intra_blob(sub_blob, rng, new_eval_fork_, Ave_blob, Ave)  # root_blob.Layers[-1] += [fork]

                        else:
                            break

    return root_blob

def eval_sub_blobs(blob_, Ave_blob): # return global dert___ and list of selected blobs

    select_blob_ = [blob for blob in blob_ if blob['Dert']['G'] > Ave_blob]
    # noisy or directional G | Ga: > intra_comp cost: rel root blob + sub_blob_

    dert___ = blob_[0]['dert___']  # dert___ per blob
    shape = dert___[-1][0].shape   # shape of previous layer' gs
    mask = reduce(lambda m, blob: m[blob['slices'] & blob['mask']],
                  blob,
                  np.ones(shape, dtype=bool))
    dert___[-1][:, mask] = ma.masked  # Mask irrelevant parts.

    return dert___, select_blob_