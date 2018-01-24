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


def read_tree(*args, **kwargs):
    """
    Read a Ttree into a DataFrame

    Parameters
    ----------
    args :
        Positional arguments passed to root_panas.read_root.
    kwargs :
        Keyword arguments passed to root_pandas.read_root.

    Returns
    -------
    df : DataFrame
        DataFrame containing data read in from tree.
    """

    # Read ROOT trees into data frames
    try:
        df = read_root(*args, **kwargs)
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
    will always choose to scale one set of weights up to match the other.
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


def read_trees(input_dir, features, signals, backgrounds, selection=None,
               negative_weight_treatment="passthrough",
               equalise_signal=True, branch_w="EvtWeight",
               col_w="MVAWeight", col_target="Signal"):
    """
    Read in Ttrees.

    Files in the input directory should be named according to the schema
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
    input_dir : string
        Directory containing input ROOT files for the classifier.
    features : list of strings
        Names of features to be used in classifier training. Should correspond
        to Ttree branches in the input files.
    signals : list of strings
        Names of processes to be considered signal.
    backgrounds : list of strings
        Names of processes to be considered background.
    selection : string, optional
        ROOT selection string specifying the cuts that should be made on
        input files. If None, no cuts are performed.
    negative_weight_treatment : "passthrough", "abs", or "reweight", optional
        How negative event weights should be treated
            "passthrough": negative weights are unaltered (default).
            "abs": the absolute value of all negative weights is taken.
            "reweight": The absolute value of all negative weights is taken.
                        The original normalisation for each process is then
                        restored by linearly scaling the resulting weights
                        down. This will fail if any processes have an overall
                        negative weight.
    equalise_signal : bool, optional
        If True (the default), the weights of the signal channels are linearly
        scaled so that the overall normalisation for both the signal and
        background channels is the same.
    branch_w : string, optional
        Name of branch in ROOT files containing event weights.
    col_w : string, optional
        Name of column in returned DataFrame containing "MVA Weights". These
        are the event weights after the transformations specfied by the
        negative_weight_treatment and equalise_signal options have taken place.
    col_target: string, optional
        Name of column inn returned DataFrame containing the target values for
        the classifier. This will be 1 for events in processes specified by
        signals and 0 otherwise.
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
        assert (reweighted >= 0).all(), \
            "Negative MVA Weights after reweight"

        return reweighted

    sig_dfs = []
    bkg_dfs = []

    root_files = glob.iglob(input_dir + r"*.root")

    for root_file in root_files:
        process = get_process_name(root_file)

        # Only include specfied processes
        if process not in signals + backgrounds:
            continue

        df = read_tree(root_file, "Ttree_{}".format(process),
                       columns=features + [branch_w], where=selection)

        if df.empty:
            continue

        # Deal with weights
        if negative_weight_treatment == "reweight":
            df[col_w] = reweight(df[branch_w])
        elif negative_weight_treatment == "abs":
            df[col_w] = np.abs(df[branch_w])
        elif negative_weight_treatment == "passthrough":
            df[col_w] = df[branch_w]
        else:
            raise ValueError("Bad value for option negative_weight_treatment:",
                             negative_weight_treatment)

        # Count events
        print("Process ", process, " contains ", len(df.index), " (",
              df[branch_w].sum(), ") events", sep='')

        # Label process
        df = df.assign(Process=process)

        # Split into signal and background
        if process in signals:
            sig_dfs.append(df)
        else:
            bkg_dfs.append(df)

    sig_df = pd.concat(sig_dfs)
    bkg_df = pd.concat(bkg_dfs)

    # Equalise signal and background weights if we were asked to
    if equalise_signal:
        sig_df[col_w], bkg_df[col_w] = balance_weights(sig_df[col_w],
                                                       bkg_df[col_w])

    # Label signal and background
    sig_df[col_target] = 1
    bkg_df[col_target] = 0

    return pd.concat([sig_df, bkg_df])


def _format_TH1_name(name, combine=True, channel="all"):
    """
    Modify name of Ttrees from input files to a format expected by combine
    or THETA.

    Parameters
    ----------
    name : string
        Name of the Ttree.
    combine : bool, optional
        If True (the default), TH1 names are formatted to be compatible with
        the Higgs Analysis Combined Limit Tool. Else, the names are compatible
        with THETA.
    channel : string, optional
        The channel contained within the histogram. Used in naming the TH1
        only.

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

    name = re.sub(r"^Ttree", "MVA_{}_".format(channel), name)
    if combine:
        name = re.sub(r"__plus$", "Up", name)
        name = re.sub(r"__minus$", "Down", name)

    return name


def col_to_TH1(x, w=None, name="MVA", title="MVA", bins=20, range=(0, 1)):
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
    bins : int, optional
        Number of bins in TH1.
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


def poisson_pseudodata(df, bins=20, range=(0, 1)):
    """
    Generate Poisson pseudodata from a DataFrame by binning the MVA
    discriminant in a TH1D and applying a Poisson randomisation to each bin.

    Parameters
    ----------
    df : DataFrame
        Dataframe containing the data to be used as a base for the pseudodata.
    bins : int, optional
        Number of bins in TH1.
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

    h = col_to_TH1(df, bins=bins, range=range)

    for i in xrange(1, h.GetNbinsX() + 1):
        try:
            h.SetBinContent(i, np.random.poisson(h.GetBinContent(i)))
        except ValueError:  # negative bin
            h.SetBinContent(i, -np.random.poisson(-h.GetBinContent(i)))

    return h


def write_root(input_dir, features, response_function, selection=None, bins=20,
               range=(0, 1), drop_nan=False, data="empty", combine=True,
               channel="all", branch_w="EvtWeight", filename="mva.root"):
    """
    Evaluate an MVA and write the result to TH1s in a ROOT file.

    Parameters
    ----------
    input_dir : string
        Directory containing input ROOT files containing data to be processed
        by the trained classifier.
    features : list of strings
        Names of features used in the classifier training. Should correspond
        to Ttree branches in the input files.
    response_function : callable
        Callable which takes a DataFrame as its argument and returns an
        array-like containing the classifier responses.
    selection : string, optional
        ROOT selection string specifying the cuts that should be made on
        input files. If None, no cuts are performed.
    bins : int, optional
        Number of bins in TH1s.
    range : (float, float), optional
        Lower and upper range of bins.
    drop_nan : bool, optional
        Controls w`Vhether events with NaN event weights should be preserved
        (the default) or dropped.
    data : "empty" or "poisson", optional
        What form the (pseudo)-data in the output ROOT files should take
            empty: Empty histograms (default).
            poisson: Sum the Monte Carlo histograms, and perform a Poisson
                     jump on each bin.
    combine : bool, optional
        If True (the default), TH1 names are formatted to be compatible with
        the Higgs Analysis Combined Limit Tool. Else, the names are compatible
        with THETA.
    channel : string, optional
        The name of the channel. Used in naming the resulting TH1s only.
    branch_w : string, optional
        Name of branch containing event weights in ROOT files.
    filename : string, optional
        Name of the output root file (including directory).

    Returns
    -------
    None
    """

    root_files = glob.iglob(input_dir + r"*.root")

    fo = ROOT.TFile(filename, "RECREATE")
    pseudo_dfs = []  # list of dataframes we'll turn into pseudodata
    data_name = "DataEG" if channel == "ee" else "DataMu"

    for root_file in root_files:
        fi = ROOT.TFile(root_file, "READ")

        # Dedupe, the input files contain duplicates for some reason...
        for tree in unique_everseen(key.ReadObj().GetName()
                                    for key in fi.GetListOfKeys()):
            df = read_tree(root_file, tree, columns=features + [branch_w],
                           where=selection)

            if df.empty:
                continue

            print("Evaluating classifier on Ttree", tree)
            df = df.assign(MVA=response_function(df))

            # Look for and handle NaN Event Weights:
            nan_weights = df[branch_w].isnull().sum()
            if nan_weights > 0:
                print("WARNING:", nan_weights, "NaN weights found")
                if drop_nan:
                    df = df[pd.notnull(df[branch_w])]

            # Trees used in pseudodata should be not systematics and not data
            if not re.search(r"(minus)|(plus)|({})$".format(data_name), tree):
                pseudo_dfs.append(df)

            tree = _format_TH1_name(tree, combine=combine, channel=channel)
            h = col_to_TH1(df.MVA, w=df[branch_w],
                           bins=bins, name=tree, title=tree, range=range)
            h.SetDirectory(fo)
            fo.cd()
            h.Write()

    data_process = "data_obs" if combine else "DATA"

    h = ROOT.TH1D()
    h.Sumw2()
    if data == "poisson":
        h = poisson_pseudodata(pd.concat(pseudo_dfs), bins=bins, range=range)
    elif data == "empty":
        h = ROOT.TH1D()
    else:
        raise ValueError("Unrecogised value for option 'data': ", data)

    h.SetName("MVA_{}__{}".format(channel, data_process))
    h.SetDirectory(fo)
    fo.cd()
    h.Write()

    fo.Close()
