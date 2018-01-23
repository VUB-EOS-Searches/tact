# -*- coding: utf-8 -*-

"""
This module contains functions and helper functions relating to the reading and
writing of ROOT files.

Note that this module uses root_numpy (and root_pandas, which depends on it)
for ROOT interop. root_numpy must be recompiled every time the ROOT version is
changed, or there may be issues.

Todo:
    * When it contains all the functionality we need (notably ROOT file
      writing), use the uproot package for interop.
"""

from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

import errno
import glob
import os
import re
from operator import truediv

import numpy as np
import pandas as pd
from more_itertools import unique_everseen
from root_numpy import array2hist
from root_pandas import read_root

import ROOT
from tact.config import cfg


def makedirs(*paths):
    """
    Creates a directory for each path given. No effect if the directory
    already exists.

    Parameters
    ----------
    paths : strings
        The path of each directory to be created.

    Returns
    -------
    None
    """

    for path in paths:
        try:
            os.makedirs(os.path.dirname(path))
        except OSError as e:
            if e.errno == errno.EEXIST and os.path.isdir(path):
                pass  # directory already exists
            else:
                raise


def read_tree(root_file, tree, selection):
    """
    Read a Ttree into a DataFrame

    Parameters
    ----------
    root_file : string
        Path of ROOT file to be read.
    tree : string
        Name of Ttree in root_file to be read.

    Returns
    -------
    df : DataFrame
        DataFrame containing data read in from tree.
    """

    # Read ROOT trees into data frames
    try:
        df = read_root(root_file, tree, where=selection,
                       columns=cfg["features"] + ["EvtWeight"])
    except (IOError, IndexError):  # failure for empty trees
        return pd.DataFrame()

    return df


def balance_weights(w1, w2):
    """
    Balance the weights in two different DataFrames so they sum to the same
    value.

    Parameters
    ----------
    w1, w2 : array-like
        Weights to be balanced.

    Returns
    -------
    w1, w2 : Series
        Adjusted weights.

    Notes
    -----
    Only one of the returned df1, df2 will have adjusted weights. The function
    will always choose to scale the weights of one DataFrame up to match the
    other.
    """

    sum1 = np.sum(w1)
    sum2 = np.sum(w2)
    scale = truediv(*sorted([sum1, sum2], reverse=True))  # always scale up

    if sum1 < sum2:
        w1 = w1 * scale
    elif sum1 > sum2:
        w2 = w2 * scale

    assert np.isclose(np.sum(w1), np.sum(w2))
    assert np.sum(w1) >= sum1
    assert np.sum(w2) >= sum2

    return w1, w2


def read_trees(selection=None):
    """
    Read in Ttrees.

    File in the input directory should be named according to the schema
    "histofile_$PROCESS.root". Within each file should be a Ttree named
    "Ttree_$PROCESS" containing event data. A branch named "EvtWeight"
    containing event weights is expected in each Ttree.

    Parameters
    ----------
    selection : string, optional
        ROOT selection string specifying cuts that should be made on read-in
        trees. If None, no cuts are made.

    Returns
    -------
    df : DataFrame
        DataFrame containing the Ttree data, MVA weights (as "MVAWeight") and
        classification flag for each event ("Signal" == 1 for signal events,
        0 otherwise).

    Notes
    -----
    Options for this function are handled entirely by the global configuration.
    """

    def get_process_name(path):
        """
        Given a path to a ROOT file, return the name of the process contained.

        Parameters
        ----------
        path : string
            Path to ROOT file.
        col_w : string, optional
            Name of column in df1 and df2 containing weights.

        Returns
        -------
        string :
            Name of process.
        """

        return re.split(r"histofile_|\.", path)[-2]

    def reweight(w):
        """
        Takes the absolute value of the supplied weights, and scales the
        resulting weights down to restore the original normalisation.

        Will fail if the normalisation is < 0.

        Parameters
        ----------
        w : Series
            Series containing weights.

        Returns
        -------
        Series
            Series with adjusted weights.
        """

        reweighted = np.abs(w)
        try:
            reweighted = reweighted * \
                    (np.sum(w) / np.sum(reweighted))
        except ZeroDivisionError:  # all weights are 0 or df is empty
            pass

        assert np.isclose(np.sum(w), np.sum(reweighted)), \
            "Bad weight renormalisation"
        assert (df["MVAWeight"] >= 0).all(), \
            "Negative MVA Weights after reweight"

        return reweighted

    sig_dfs = []
    bkg_dfs = []

    root_files = glob.iglob(cfg["input_dir"] + r"*.root")

    for root_file in root_files:
        process = get_process_name(root_file)

        # Only include specfied processes
        if process not in cfg["signals"] + cfg["backgrounds"]:
            continue

        df = read_tree(root_file, "Ttree_{}".format(process), selection)

        if df.empty:
            continue

        # Deal with weights
        if cfg["negative_weight_treatment"] == "reweight":
            df.assign(MVAWeight=reweight(df.EvtWeight))
        elif cfg["negative_weight_treatment"] == "abs":
            df["MVAWeight"] = np.abs(df.EvtWeight)
        elif cfg["negative_weight_treatment"] == "passthrough":
            df["MVAWeight"] = df.EvtWeight
        else:
            raise ValueError("Bad value for option negative_weight_treatment:",
                             cfg["negative_weight_treatment"])

        # Count events
        print("Process ", process, " contains ", len(df.index), " (",
              df.EvtWeight.sum(), ") events", sep='')

        # Label process
        df = df.assign(Process=process)

        # Split into signal and background
        if process in cfg["signals"]:
            sig_dfs.append(df)
        else:
            bkg_dfs.append(df)

    sig_df = pd.concat(sig_dfs)
    bkg_df = pd.concat(bkg_dfs)

    # Equalise signal and background weights if we were asked to
    if cfg["equalise_signal"]:
        sig_df.MVAWeight, bkg_df.MVAWeight = balance_weights(sig_df.MVAWeight,
                                                             bkg_df.MVAWeight)

    # Label signal and background
    sig_df["Signal"] = 1
    bkg_df["Signal"] = 0

    return pd.concat([sig_df, bkg_df])


def _format_TH1_name(name):
    """
    Modify name of Ttrees from input files to a format expected by combine
    or THETA.

    Parameters
    ----------
    name : string
        Name of the Ttree.
    channel : "ee" or "mumu"
        The channel contained within the histogram.

    Returns
    -------
    name : The name of the TH1D

    Notes
    -----
    The input name is expected to be in the format:
        Ttree__$PROCESS
    for each process and raw data or
        Ttree__$PROCESS__$SYSTEMATIC__$PLUSMINUS
    for systematics where $PLUSMINUS is plus for 1σ up and minus for 1σ down.
    Ttree is replaced with MVA_$CHANNEL_ and __plus/__minus to Up/Down if the
    combine flag is set.
    """

    name = re.sub(r"^Ttree", "MVA_{}_".format(cfg["channel"]), name)
    if cfg["root_out"]["combine"]:
        name = re.sub(r"__plus$", "Up", name)
        name = re.sub(r"__minus$", "Down", name)

    return name


def col_to_TH1(x, w=None, name="MVA", title="MVA", bins=200, range=(0, 1)):
    """
    Write data in col_x to a TH1

    Parameters
    ----------
    x : Series
        Data to be binned.
    w : Series
        Weights. If None, then samples are equally weighted.
    name : string, optional
        Name of TH1.
    title : string, optional
        Title of TH1.
    range : (float, float), optional
        Lower and upper range of bins.

    Returns
    -------
    h : TH1D
        TH1D of MVA discriminant.

    Notes
    -----
    Uses array2hist for speed, and as such does not preserve the total number
    of entries. The number of entries will be listed as the number of bins in
    the final histogram. This should not affect the expected significance as
    the weighted contents and error of each bin is preserved.
    """

    contents = np.histogram(x, bins=bins, range=range,
                            weights=w)[0]
    errors, bin_edges = np.histogram(x, bins=bins, range=range,
                                     weights=np.power(w, 2))
    errors = np.sqrt(errors)

    h = ROOT.TH1D(name, title, len(bin_edges) - 1, bin_edges)
    h.Sumw2()
    array2hist(contents, h, errors=errors)
    return h


def poisson_pseudodata(df, range=(0, 1)):
    """
    Generate Poisson pseudodata from a DataFrame by binning the MVA
    discriminant in a TH1D and applying a Poisson randomisation to each bin.

    Parameters
    ----------
    df : DataFrame
        Dataframe containing the data to be used as a base for the pseudodata.
    range : (float, float), optional
        Lower and upper range of bins.

    Returns
    -------
    h : TH1D
        TH1D containing pesudodata.

    Notes
    -----
    Should only be used in THETA.
    """

    h = col_to_TH1(df, range=range)

    for i in xrange(1, h.GetNbinsX() + 1):
        try:
            h.SetBinContent(i, np.random.poisson(h.GetBinContent(i)))
        except ValueError:  # negative bin
            h.SetBinContent(i, -np.random.poisson(-h.GetBinContent(i)))

    return h


def write_root(response_function, selection,
               col_w="EvtWeight", range=(0, 1), filename="mva.root"):
    """
    Evaluate an MVA and write the result to TH1s in a ROOT file.

    Parameters
    ----------
    response_function : callable
        Callable which takes a dataframe as its argument and returns an
        array-like containing the classifier responses.
    col_w : string, optional
        Name of branch containing event weights in ROOT files.
    range : (float, float), optional
        Lower and upper range of bins.
    filename : string, optional
        Name of the output root file (including directory).

    Returns
    -------
    None
    """

    features = cfg["features"]

    root_files = glob.iglob(cfg["input_dir"] + r"*.root")

    fo = ROOT.TFile(filename, "RECREATE")
    pseudo_dfs = []  # list of dataframes we'll turn into pseudodata
    data_name = "DataEG" if cfg["channel"] == "ee" else "DataMu"

    for root_file in root_files:
        fi = ROOT.TFile(root_file, "READ")

        # Dedupe, the input files contain duplicates for some reason...
        for tree in unique_everseen(key.ReadObj().GetName()
                                    for key in fi.GetListOfKeys()):
            df = read_tree(root_file, tree, selection)

            if df.empty:
                continue

            print("Evaluating classifier on Ttree", tree)
            df = df.assign(MVA=response_function(df[features]))

            # Look for and handle NaN Event Weights:
            nan_weights = df[col_w].isnull().sum()
            if nan_weights > 0:
                print("WARNING:", nan_weights, "NaN weights found")
                if cfg["root_out"]["drop_nan"]:
                    df = df[pd.notnull(df[col_w])]

            # Trees used in pseudodata should be not systematics and not data
            if not re.search(r"(minus)|(plus)|({})$".format(data_name), tree):
                pseudo_dfs.append(df)

            tree = _format_TH1_name(tree)
            h = col_to_TH1(df.MVA, w=df.EvtWeight,
                           bins=cfg["root_out"]["bins"],
                           name=tree, title=tree, range=range)
            h.SetDirectory(fo)
            fo.cd()
            h.Write()

    data_process = "data_obs" if cfg["root_out"]["combine"] else "DATA"

    h = ROOT.TH1D()
    h.Sumw2()
    if cfg["root_out"]["data"] == "poisson":
        h = poisson_pseudodata(pd.concat(pseudo_dfs), range=range)
    elif cfg["root_out"]["data"] == "empty":
        h = ROOT.TH1D()
    else:
        raise ValueError("Unrecogised value for option 'data': ",
                         cfg["root_out"]["data"])

    h.SetName("MVA_{}__{}".format(cfg["channel"], data_process))
    h.SetDirectory(fo)
    fo.cd()
    h.Write()

    fo.Close()
